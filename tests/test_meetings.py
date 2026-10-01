from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import wave

PROJECT = Path(__file__).resolve().parents[1]
PLUGIN = PROJECT / "plugins/ai-meeting-notes"
sys.path.insert(0, str(PLUGIN / "server"))
from core import MeetingError, Store, UPLOAD_LIMIT, digest, pack_chunks, read_json, write_json
from main import dispatch, response
from worker import prepare, work


class MeetingsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "cache")
        self.store.configure_destination("page_testmeetings")
        self.source = self.root / "source.txt"
        self.source.write_text("We agreed to launch on Friday.\n\nMaya will send the draft; no deadline was stated.\n\nThe vendor price is unclear.")
        self.mid = self.store.import_file(str(self.source), meeting_date="2026-09-29")["meeting"]["id"]
        self.folder = self.store.folder(self.mid)
        self.revision = self.store.read_transcript(self.mid)["revision"]
        self.notes = {"summary": [{"text": "The team discussed launch preparation.", "evidence": [0, 1]}],
                      "topics": [{"topic": "Launch", "points": [{"text": "Friday is the agreed launch day.", "evidence": [0]}]}],
                      "decisions": [{"text": "Launch on Friday.", "evidence": [0]}],
                      "actions": [{"task": "Send the draft.", "owner": "Maya", "due_date": None, "evidence": [1]}],
                      "open_questions": [], "uncertainties": [{"text": "Vendor price is unclear.", "evidence": [2]}]}

    def save(self):
        return self.store.save_notes(self.mid, self.revision, self.notes)

    def test_destinations_are_private_and_isolated_between_users(self):
        other = Store(self.root / "other-user")
        self.assertNotIn("meetings_page_id", other.status()["destination"])
        other.configure_destination("page_othermeetings")
        self.assertEqual(self.store.status()["destination"]["meetings_page_id"], "page_testmeetings")
        self.assertEqual(Store(other.home).status()["destination"]["meetings_page_url"],
                         "https://chatgpt.com/space/page_othermeetings")

    def test_destination_change_does_not_redirect_running_publication(self):
        write_json(self.folder / "finish-job.json", {"state": "running", "pid": os.getpid()})
        with self.assertRaises(MeetingError):
            self.store.configure_destination("page_othermeetings")
        self.assertEqual(self.store.configuration()["meetings_page_id"], "page_testmeetings")

    def test_invalid_destination_does_not_write_settings(self):
        original = read_json(self.store.home / "settings.json")
        for page in ("../outside", "https://example.com", "", None):
            with self.assertRaises(MeetingError):
                dispatch(self.store, "configure_meetings_page", {"meetings_page_id": page})
        self.assertEqual(read_json(self.store.home / "settings.json"), original)

    def prep(self):
        self.save()
        work(self.store, self.mid, "prepare")
        return self.store.publication(self.mid)

    def test_source_preserved_and_original_unchanged(self):
        self.assertEqual((self.folder / "source.txt").read_text(), self.source.read_text())
        self.assertEqual(self.store.status(self.mid)["meeting"]["meeting_date"], "2026-09-29")
        self.assertEqual(self.store.status(self.mid)["recording"]["state"], "not_recorded")

    def test_empty_import_does_not_create_meeting(self):
        self.source.write_text("   ")
        before = len(self.store.list_meetings()["items"])
        with self.assertRaises(MeetingError):
            self.store.import_file(str(self.source))
        self.assertEqual(before, len(self.store.list_meetings()["items"]))

    def test_large_transcript_pagination_no_missing_segments(self):
        self.source.write_text("\n\n".join("段落" * 900 for _ in range(25)))
        mid = self.store.import_file(str(self.source))["meeting"]["id"]
        collected, start = [], 0
        while True:
            page = self.store.read_transcript(mid, start, 100)
            self.assertLess(len(json.dumps(page, ensure_ascii=False).encode()), 18000)
            collected += page["segments"]
            if page["next_segment"] is None:
                break
            start = page["next_segment"]
        self.assertEqual([s["id"] for s in collected], list(range(page["total_segments"])))
        self.assertEqual("".join(s["text"] for s in collected), self.source.read_text().replace("\n\n", ""))

    def test_path_traversal_rejected(self):
        for mid in ("../source", "../../etc/passwd", "/tmp/outside", "not-a-meeting", True):
            with self.assertRaises(MeetingError):
                self.store.folder(mid)

    def test_uncited_and_missing_source_rejected(self):
        for evidence in ([], [99], [True], ["1"]):
            notes = copy.deepcopy(self.notes)
            notes["actions"][0]["evidence"] = evidence
            with self.assertRaises(MeetingError):
                self.store.save_notes(self.mid, self.revision, notes)
        self.assertFalse((self.folder / "notes.json").exists())

    def test_stale_transcript_rejected(self):
        self.store.put_transcript(self.folder, [{"text": "Different content."}], "import")
        with self.assertRaises(MeetingError):
            self.save()

    def test_unknown_due_date_stays_unknown(self):
        self.save()
        output = (self.folder / "notes.md").read_text()
        self.assertIn("due: Not stated", output)
        self.assertIn("untimed, segment 1", output)
        self.assertTrue(self.store.status(self.mid)["notes_current"])

    def test_invalid_dates_rejected(self):
        self.notes["actions"][0]["due_date"] = "Friday"
        with self.assertRaises(MeetingError):
            self.save()

    def test_note_revisions_retained(self):
        self.save()
        self.notes["summary"][0]["text"] = "The team prepared a Friday launch."
        self.save()
        revisions = list((self.folder / "revisions").glob("*.json"))
        self.assertEqual(len(revisions), 1)
        self.assertIn("discussed", read_json(revisions[0])["notes"]["summary"][0]["text"])

    def test_cannot_process_active_recording(self):
        write_json(self.folder / "recorder.json", {"state": "recording", "pid": os.getpid()})
        with self.assertRaises(MeetingError):
            self.store.launch_job(self.mid, "transcribe")
        result = self.store.stop(self.mid)
        self.assertEqual(result["state"], "stop_requested")
        self.assertTrue((self.folder / "STOP").exists())

    def test_interrupted_capture_detected(self):
        write_json(self.folder / "recorder.json", {"state": "recording", "pid": 99999999})
        self.assertEqual(self.store.status(self.mid)["recording"]["state"], "interrupted")

    def test_worker_failure_reported(self):
        (self.folder / "source.json").unlink()
        with self.assertRaises(MeetingError):
            work(self.store, self.mid, "transcribe")
        self.assertEqual(self.store.status(self.mid)["transcription"]["state"], "error")

    def test_text_publication_has_complete_transcript(self):
        prepared = self.prep()
        self.assertEqual([a["kind"] for a in prepared["manifest"]["assets"]], ["transcript"])
        self.assertIn("vendor price is unclear", "\n".join(prepared["transcript_chunks"]))
        self.assertEqual(prepared["next_chunk"], None)
        self.assertEqual(prepared["receipts"]["state"], "pending")

    def test_complete_requires_real_matching_asset_receipts(self):
        prepared = self.prep()
        pid = "page_test123"
        url = "https://chatgpt.com/space/" + pid
        self.store.receipt(self.mid, "page", pid, url)
        with self.assertRaises(MeetingError):
            self.store.receipt(self.mid, "complete", pid, url, verified=True)
        asset = prepared["manifest"]["assets"][0]
        with self.assertRaises(MeetingError):
            self.store.receipt(self.mid, "asset", pid, asset_name=asset["name"], reference="library-file:abc", sha256="wrong", verified=True)
        with self.assertRaises(MeetingError):
            self.store.receipt(self.mid, "asset", pid, asset_name=asset["name"], reference="/tmp/file", sha256=asset["sha256"], verified=True)
        self.store.receipt(self.mid, "asset", pid, asset_name=asset["name"], reference="library-file:abc", sha256=asset["sha256"], verified=True)
        completed = self.store.receipt(self.mid, "complete", pid, url, verified=True)
        self.assertEqual(completed["state"], "complete")

    def test_page_identity_cannot_change_during_retry(self):
        self.store.receipt(self.mid, "page", "page_a")
        with self.assertRaises(MeetingError):
            self.store.receipt(self.mid, "page", "page_b")
        self.assertEqual(self.store.status(self.mid)["publication"]["page_id"], "page_a")

    def test_updated_notes_invalidate_publication(self):
        self.prep()
        self.save()
        self.assertEqual(self.store.status(self.mid)["preparation"]["state"], "not_started")
        self.assertEqual(self.store.status(self.mid)["publication"]["state"], "pending")

    def test_byte_chunks_no_truncation(self):
        items = ["é" * 500, "中" * 300, "hello"]
        chunks = pack_chunks(items, 1500)
        self.assertTrue(all(len(c.encode()) <= 1500 for c in chunks))
        self.assertEqual("\n\n".join(chunks), "\n\n".join(items))

    def test_mcp_protocol_and_error_envelopes(self):
        initialized = response(self.store, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(initialized["result"]["protocolVersion"], "2025-06-18")
        self.assertIsNone(response(self.store, {"jsonrpc": "2.0", "method": "notifications/initialized"}))
        unknown = response(self.store, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "unknown"}})
        self.assertTrue(unknown["result"]["isError"])
        invalid = response(self.store, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "save_notes", "arguments": {"meeting_id": self.mid}}})
        self.assertTrue(invalid["result"]["isError"])

    def test_stdio_stdout_is_only_mcp_messages(self):
        messages = ["{invalid", json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})]
        result = subprocess.run([sys.executable, str(PLUGIN / "server/main.py"), "--stdio", "--home", str(self.store.home)],
                                input="\n".join(messages) + "\n", capture_output=True, text=True, check=True)
        output = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(output), 3)
        self.assertEqual(output[0]["error"]["code"], -32700)
        self.assertEqual(len(output[1]["result"]["tools"]), 13)
        self.assertEqual(output[2]["result"], {})

    def test_automatic_workflow_launch_is_deduplicated(self):
        write_json(self.folder / "recorder.json", {"state": "stopped"})
        with patch("core.subprocess.Popen", return_value=Mock(pid=os.getpid())) as launch:
            first = self.store.stop(self.mid)
            second = self.store.finish_meeting(self.mid)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(first["attempt"], second["attempt"])
        self.assertTrue((self.folder / "AUTO-PROCESS").exists())
        self.assertEqual(self.store.status(self.mid)["workflow"]["state"], "queued")

    def test_automatic_workflow_never_starts_before_capture_closes(self):
        write_json(self.folder / "recorder.json", {"state": "stopping", "pid": os.getpid()})
        with patch("core.subprocess.Popen") as launch, self.assertRaises(MeetingError):
            self.store.finish_meeting(self.mid)
        launch.assert_not_called()

    def test_retry_is_explicit_and_keeps_publication_receipts(self):
        write_json(self.folder / "finish-job.json", {"state": "error", "error": "Disconnected"})
        receipt = {"state": "page_created", "page_id": "page_existing", "assets": {"recording": {"reference": "library-file:saved"}}}
        write_json(self.folder / "publication.json", receipt)
        with patch("core.subprocess.Popen", return_value=Mock(pid=os.getpid())) as launch:
            self.assertEqual(self.store.finish_meeting(self.mid)["state"], "error")
            launch.assert_not_called()
            self.store.finish_meeting(self.mid, retry=True)
        self.assertEqual(read_json(self.folder / "publication.json"), receipt)

    def test_background_tools_are_scoped_to_one_meeting(self):
        with patch.dict(os.environ, {"MEETING_NOTES_AUTOMATION_ID": self.mid}):
            dispatch(self.store, "meeting_status", {"meeting_id": self.mid})
            for name, args in [("meeting_status", {}), ("stop_recording", {"meeting_id": self.mid}),
                               ("meeting_status", {"meeting_id": "f" * 32})]:
                with self.assertRaises(MeetingError):
                    dispatch(self.store, name, args)

    def test_auto_worker_rejects_model_success_without_publication(self):
        from workflow import finish
        write_json(self.folder / "finish-job.json", {"state": "queued", "attempt": "a"})
        with patch("workflow.publish"), self.assertRaises(MeetingError):
            finish(self.store, self.mid, "a")
        self.assertEqual(read_json(self.folder / "finish-job.json")["state"], "error")
        self.assertNotEqual(self.store.status(self.mid)["publication"]["state"], "complete")

    def test_auto_worker_finishes_only_with_current_verified_receipts(self):
        from workflow import finish
        manifest = self.prep()["manifest"]
        def publish(store, folder, progress):
            store.receipt(self.mid, "page", "page_auto", "https://chatgpt.com/space/page_auto")
            for asset in manifest["assets"]:
                store.receipt(self.mid, "asset", "page_auto", asset_name=asset["name"],
                              sha256=asset["sha256"], reference="library-file:auto", verified=True)
            store.receipt(self.mid, "complete", "page_auto", verified=True)
        write_json(self.folder / "finish-job.json", {"state": "queued", "attempt": "a"})
        with patch("workflow.publish", side_effect=publish):
            finish(self.store, self.mid, "a")
        job = self.store.status(self.mid)["workflow"]
        self.assertEqual(job["state"], "complete")
        with patch("core.subprocess.Popen") as launch:
            result = self.store.finish_meeting(self.mid, retry=True)
        launch.assert_not_called()
        self.assertEqual(result["page_url"], job["page_url"])

    def test_pending_stop_dispatch_is_recovered_without_capture(self):
        write_json(self.folder / "recorder.json", {"state": "stopped"})
        (self.folder / "AUTO-PROCESS").touch()
        with patch("core.subprocess.Popen", return_value=Mock(pid=os.getpid())) as launch:
            self.store.resume_pending()
        self.assertEqual(launch.call_count, 1)
        self.assertIn("finish", launch.call_args.args[0])

    def test_background_command_keeps_sandbox_and_auth_in_codex(self):
        from workflow import command
        with patch("workflow.shutil.which", return_value="/usr/local/bin/codex"), patch("workflow.subprocess.run", return_value=Mock(stdout='{"installed": []}')):
            cmd = command(self.store, self.folder)
        self.assertEqual(cmd[cmd.index("--sandbox") + 1], "read-only")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", cmd)
        self.assertIn("apps._default.enabled=false", cmd)
        config = next(c for c in cmd if c.startswith("mcp_servers.meeting_pipeline="))
        import tomllib
        settings = tomllib.loads(config)["mcp_servers"]["meeting_pipeline"]
        self.assertEqual(settings["env"]["MEETING_NOTES_AUTOMATION_ID"], self.mid)
        self.assertNotIn("start_recording", settings["enabled_tools"])
        pages = tomllib.loads(next(c for c in cmd if c.startswith("apps.connector_openai_pages.tools=")))["apps"]["connector_openai_pages"]["tools"]
        self.assertTrue(pages["edit_page"]["enabled"])
        self.assertEqual(pages["edit_page"]["approval_mode"], "approve")
        self.assertNotIn("trash_page", pages)
        self.assertNotIn("update_page_sharing", pages)
        self.assertIn("apps.connector_openai_pages.default_tools_enabled=false", cmd)

    def test_legacy_verified_receipts_retain_access_and_size_for_retry(self):
        manifest = self.prep()["manifest"]
        self.store.receipt(self.mid, "page", "page_retry", "https://chatgpt.com/space/page_retry")
        for asset in manifest["assets"]:
            receipt = self.store.receipt(self.mid, "asset", "page_retry", asset_name=asset["name"],
                                        reference="library-file:retry", sha256=asset["sha256"], verified=True,
                                        byte_size=asset["byte_size"])
        for saved in receipt["assets"].values():
            saved.pop("byte_size")
            saved.pop("file_access_confirmed")
        write_json(self.folder / "publication.json", receipt)
        recovered = self.store.publication(self.mid)["receipts"]
        for asset in manifest["assets"]:
            saved = recovered["assets"][asset["name"]]
            self.assertTrue(saved["file_access_confirmed"])
            self.assertEqual(saved["byte_size"], asset["byte_size"])
            self.assertEqual(saved["reference"], "library-file:retry")

    def test_wrong_upload_size_is_rejected(self):
        asset = self.prep()["manifest"]["assets"][0]
        with self.assertRaises(MeetingError):
            self.store.receipt(self.mid, "asset", "page_retry", asset_name=asset["name"],
                               reference="library-file:retry", sha256=asset["sha256"], verified=True,
                               byte_size=asset["byte_size"]+1)

    def test_audio_prepare_upload_parts_cover_long_meeting(self):
        # A quiet WAV is synthetic audio; this test does not open microphone/capture APIs.
        with wave.open(str(self.folder / "audio.wav"), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            for _ in range(601):
                wav.writeframesraw(bytes(32000))
        (self.folder / "source.json").unlink()
        self.save()
        prepare(self.store, self.folder)
        manifest = read_json(self.folder / "publication-manifest.json")
        audio = [a for a in manifest["assets"] if a["kind"] == "recording"]
        self.assertEqual(len(audio), 2)
        self.assertEqual([a["start_seconds"] for a in audio], [0, 600])
        self.assertTrue(all(0 < a["byte_size"] < UPLOAD_LIMIT for a in audio))
        lengths = [float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", a["path"]])) for a in audio]
        self.assertAlmostEqual(sum(lengths), 601, delta=0.3)

    def test_evidence_links_use_confirmed_transcript_reference(self):
        prepared = self.prep()
        asset = prepared["manifest"]["assets"][0]
        self.store.receipt(self.mid, "asset", "page_test", asset_name=asset["name"], reference="library-file:testfile", sha256=asset["sha256"], verified=True)
        content = self.store.publication(self.mid)["notes_markdown"]
        self.assertIn("[untimed, segment 1](library-file:testfile)", content)

    def test_stopping_detector_does_not_start_audio(self):
        result = self.store.detection(enabled=False)
        self.assertFalse(result["recording_started"])
        self.assertTrue((self.store.home / "STOP-WATCHER").exists())
        self.assertEqual(self.store.status()["active_recordings"], [])


if __name__ == "__main__":
    unittest.main()
