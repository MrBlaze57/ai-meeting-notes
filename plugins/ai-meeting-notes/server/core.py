"""Local capture/processing cache. Spaces publication is performed by Codex's Pages app."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
UPLOAD_LIMIT = 10 * 1024 * 1024
ACTIVE = {"starting", "recording", "stopping"}
MODELS = {"tiny", "base", "small", "medium", "large", "turbo"}


class MeetingError(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".writing-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def text(value, name, max_len=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise MeetingError(f"{name} must be nonempty text, at most {max_len} characters.")
    return value.strip()


def integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise MeetingError(f"{name} must be an integer between {low} and {high}.")
    return value


def stamp(seconds):
    if seconds is None:
        return "untimed"
    value = max(0, int(seconds))
    h, rest = divmod(value, 3600)
    m, s = divmod(rest, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def md_text(value):
    return re.sub(r"([\\`*_{}\[\]<>|])", r"\\\1", str(value)).replace("\n", " ")


def transcript_lines(transcript):
    return [f"**[{stamp(s['start'])}–{stamp(s['end'])}] Segment {s['id']}**\n\n{md_text(s['text'])}" for s in transcript["segments"]]


def pack_chunks(items, max_bytes=12000):
    result, current = [], ""
    for item in items:
        if len(item.encode("utf-8")) > max_bytes:
            raise MeetingError("A transcript segment is too large; reimport it in smaller paragraphs.")
        combined = (current + "\n\n" if current else "") + item
        if len(combined.encode("utf-8")) > max_bytes:
            result.append(current)
            current = item
        else:
            current = combined
    if current:
        result.append(current)
    return result


class Store:
    def __init__(self, home=None):
        self.home = Path(home or os.environ.get("MEETING_NOTES_HOME", Path.home() / "Library/Application Support/AI Meeting Notes/cache")).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)

    def configuration(self):
        # Destinations belong to the user cache, never the distributable package.
        defaults = read_json(ROOT / "config.json", {})
        return {**defaults, **read_json(self.home / "settings.json", {})}

    def configure_destination(self, meetings_page_id):
        if not isinstance(meetings_page_id, str) or not re.fullmatch(r"page_[a-zA-Z0-9]+", meetings_page_id):
            raise MeetingError("Use the verified Meetings Page ID returned by Pages.")
        with self.lock():
            if any(self.job(path.parent, "finish")["state"] in {"queued", "running"}
                   for path in self.home.glob("*/meeting.json")):
                raise MeetingError("Wait for automatic publication to finish before changing the destination.")
            settings = read_json(self.home / "settings.json", {})
            settings.update(meetings_page_id=meetings_page_id,
                            meetings_page_url="https://chatgpt.com/space/" + meetings_page_id)
            write_json(self.home / "settings.json", settings)
        return self.configuration()

    @contextlib.contextmanager
    def lock(self):
        with (self.home / ".lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def folder(self, meeting_id):
        if not isinstance(meeting_id, str) or not re.fullmatch(r"[0-9a-f]{32}", meeting_id):
            raise MeetingError("Invalid meeting ID.")
        folder = self.home / meeting_id
        if not (folder / "meeting.json").is_file() or folder.is_symlink():
            raise MeetingError("Meeting not found.")
        return folder

    def create(self, title, mode, participants=None, meeting_date=None):
        title = text(title, "title", 200)
        participants = participants or []
        if not isinstance(participants, list) or len(participants) > 100:
            raise MeetingError("participants must be a list of names.")
        participants = [text(p, "participant", 120) for p in participants]
        date = meeting_date or dt.datetime.now().astimezone().date().isoformat()
        try:
            dt.date.fromisoformat(date)
        except (ValueError, TypeError):
            raise MeetingError("meeting_date must use YYYY-MM-DD.")
        meeting_id = uuid.uuid4().hex
        folder = self.home / meeting_id
        folder.mkdir(mode=0o700)
        meta = {"id": meeting_id, "title": title, "mode": mode, "participants": participants,
                "created_at": now(), "meeting_date": date, "date_basis": "user" if meeting_date else "capture/import date",
                "timezone": str(dt.datetime.now().astimezone().tzinfo)}
        write_json(folder / "meeting.json", meta)
        return folder, meta

    def recorder(self, folder):
        rec = read_json(folder / "recorder.json", {"state": "not_recorded"})
        if rec["state"] in ACTIVE:
            started = read_json(folder / "meeting.json")["created_at"]
            age = time.time() - dt.datetime.fromisoformat(started).timestamp()
            if (rec.get("pid") and not alive(rec["pid"])) or (not rec.get("pid") and age > 120):
                rec = {**rec, "state": "interrupted", "error": "Recorder stopped unexpectedly. Existing audio is retained for recovery."}
        return rec

    def job(self, folder, phase):
        job = read_json(folder / f"{phase}-job.json", {"state": "not_started"})
        if job["state"] in {"queued", "running"} and not alive(job.get("pid")):
            # Allow launch to initialize its PID before diagnosing interruption.
            if time.time() - job.get("started_epoch", 0) > 5:
                job = {**job, "state": "error", "error": "Processing worker exited. Retry to resume from saved files."}
        return job

    def status(self, meeting_id=None):
        config = self.configuration()
        if meeting_id:
            folder = self.folder(meeting_id)
            meta = read_json(folder / "meeting.json")
            transcript = read_json(folder / "transcript.json")
            notes = read_json(folder / "notes.json")
            publication = read_json(folder / "publication.json", {"state": "not_published", "assets": {}})
            return {"meeting": meta, "recording": self.recorder(folder), "transcription": self.job(folder, "transcribe"),
                    "preparation": self.job(folder, "prepare"), "workflow": self.job(folder, "finish"),
                    "transcript_revision": transcript.get("revision") if transcript else None,
                    "notes_current": bool(notes and transcript and notes["transcript_revision"] == transcript["revision"]),
                    "publication": publication, "destination": config}
        active = []
        for path in self.home.glob("*/meeting.json"):
            rec = self.recorder(path.parent)
            if rec["state"] in ACTIVE:
                active.append({"meeting": read_json(path), "recording": rec})
        return {"cache_path": str(self.home), "destination": config, "active_recordings": active,
                "dependencies": {name: shutil.which(name) for name in ("ffmpeg", "ffprobe", "whisper", "swift", "codex")},
                "recorder_built": (ROOT / "bin/MeetingRecorder.app/Contents/MacOS/MeetingRecorder").is_file(),
                "recording_platform": "macOS 15 or later", "notes_engine": "Codex host", "transcription_engine": "local Whisper",
                "meeting_detection": self.detection_status()}

    def detection_status(self):
        status = read_json(self.home / "watcher.json", {"state": "not_started"})
        if status.get("pid") and not alive(status["pid"]):
            status = {**status, "state": "stopped"}
        return status

    def detection(self, enabled=True, automatic=False):
        if type(enabled) is not bool:
            raise MeetingError("enabled must be true or false.")
        if not enabled:
            (self.home / "STOP-WATCHER").touch(mode=0o600)
            return {"state": "stop_requested", "recording_started": False}
        if sys.platform != "darwin":
            raise MeetingError("Meeting detection requires macOS.")
        with self.lock():
            current = self.detection_status()
            if automatic and (read_json(self.home / "watcher.json", {}).get("state") == "disabled" or (self.home / "STOP-WATCHER").exists()):
                return {"state": "disabled", "recording_started": False}
            if current["state"] in {"watching", "needs_accessibility"} and alive(current.get("pid")):
                return current
            stop = self.home / "STOP-WATCHER"
            if stop.exists():
                stop.unlink()
            app = ROOT / "bin/MeetingRecorder.app"
            if not (app / "Contents/MacOS/MeetingRecorder").is_file():
                raise MeetingError("Build the recorder before enabling detection.")
            cmd = ["/usr/bin/open", "-n", str(app), "--args", "--watch", "--cache", str(self.home)]
            if not automatic:
                cmd += ["--enable-detection"]
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=15)
        return {"state": "starting", "recording_started": False,
                "next_step": "Enable MeetingRecorder in macOS Accessibility if prompted. Detection never starts audio without your Start click."}

    def start(self, title="Meeting", mode="online", participants=None, max_duration_minutes=180):
        if sys.platform != "darwin":
            raise MeetingError("Live recording requires macOS 15 or later; file imports work on other platforms.")
        if mode not in {"online", "in_person"}:
            raise MeetingError("mode must be online or in_person.")
        integer(max_duration_minutes, "max_duration_minutes", 1, 480)
        app = ROOT / "bin/MeetingRecorder.app"
        if not (app / "Contents/MacOS/MeetingRecorder").is_file():
            raise MeetingError("Recorder is not built. Run script/build_and_run.sh --build-only in the project.")
        with self.lock():
            if self.status()["active_recordings"]:
                raise MeetingError("A recording is already active. Stop it before starting another.")
            folder, meta = self.create(title, mode, participants)
            write_json(folder / "recorder.json", {"state": "starting", "heartbeat": time.time(), "tracks": {}})
            try:
                subprocess.run(["/usr/bin/open", "-n", str(app), "--args", "--capture-dir", str(folder), "--mode", mode,
                                "--max-minutes", str(max_duration_minutes)], check=True, capture_output=True, text=True, timeout=15)
            except (OSError, subprocess.SubprocessError) as error:
                write_json(folder / "recorder.json", {"state": "error", "error": str(error)})
                raise MeetingError("The recorder could not launch. Audio has not started.") from error
        return {"meeting": meta, "state": "starting", "next_step": "Check meeting_status; capture starts after macOS permissions are granted."}

    def stop(self, meeting_id):
        folder = self.folder(meeting_id)
        rec = self.recorder(folder)
        if rec["state"] not in ACTIVE:
            if rec["state"] == "stopped":
                return self.finish_meeting(meeting_id)
            return {"meeting_id": meeting_id, "state": rec["state"], "next_step": "Transcribe saved audio if present."}
        (folder / "STOP").touch(mode=0o600)
        return {"meeting_id": meeting_id, "state": "stop_requested", "next_step": "The recorder will automatically transcribe, write notes, and publish to Meetings after closing the audio files. Do not start a competing publication."}

    def finish_meeting(self, meeting_id, retry=False):
        """Launch one durable post-stop workflow, independently of the chat/MCP lifetime."""
        if type(retry) is not bool:
            raise MeetingError("retry must be true or false.")
        folder = self.folder(meeting_id)
        with self.lock():
            rec = self.recorder(folder)
            if rec["state"] not in {"stopped", "not_recorded"}:
                raise MeetingError("Audio must be finalized successfully before automatic processing.")
            status = self.status(meeting_id)
            pub = status["publication"]
            notes = read_json(folder / "notes.json", {})
            if (pub.get("state") == "complete" and status["notes_current"]
                    and pub.get("transcript_revision") == status["transcript_revision"]
                    and pub.get("notes_revision") == notes.get("revision")):
                result = {"meeting_id": meeting_id, "state": "complete", "phase": "complete", "page_url": pub["page_url"], "finished_at": now()}
                write_json(folder / "finish-job.json", result)
                return result
            job = self.job(folder, "finish")
            if job["state"] in {"queued", "running"} or (job["state"] == "error" and not retry):
                return {"meeting_id": meeting_id, **job}
            if alive(job.get("codex_pid")):
                raise MeetingError("An earlier publication is still running. Wait for it to exit before Retry.")
            (folder / "AUTO-PROCESS").touch(mode=0o600)
            token = uuid.uuid4().hex
            job = {"state": "queued", "phase": "transcribe", "attempt": token,
                   "started_at": now(), "started_epoch": time.time()}
            write_json(folder / "finish-job.json", job)
            cmd = [sys.executable, str(ROOT / "server/main.py"), "--worker", "finish", "--meeting", meeting_id,
                   "--home", str(self.home), "--attempt", token]
            try:
                with (folder / "finish.log").open("a") as log:
                    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                # The worker takes this same lock before it starts, so PID cannot overwrite its progress.
                job["pid"] = proc.pid
                write_json(folder / "finish-job.json", job)
            except OSError as error:
                job.update(state="error", error=str(error))
                write_json(folder / "finish-job.json", job)
                raise MeetingError("Could not start automatic processing. Audio is saved; use Retry in the recorder.") from error
        return {"meeting_id": meeting_id, **job}

    def resume_pending(self):
        """Recover a crash between audio finalization and dispatch; never start capture."""
        for marker in self.home.glob("*/AUTO-PROCESS"):
            folder = marker.parent
            if not (folder / "meeting.json").exists() or self.recorder(folder)["state"] not in {"stopped", "not_recorded"}:
                continue
            raw = read_json(folder / "finish-job.json", {"state": "not_started"})
            job = self.job(folder, "finish")
            if job["state"] == "not_started" or (raw["state"] in {"queued", "running"} and job["state"] == "error"):
                try:
                    self.finish_meeting(folder.name, retry=True)
                except Exception as error:
                    print("Automatic meeting recovery: " + str(error), file=sys.stderr)

    def put_transcript(self, folder, segments, engine):
        clean = []
        for source in segments:
            content = str(source.get("text", "")).strip()
            if not content:
                continue
            start, end = source.get("start"), source.get("end")
            if start is not None and (not isinstance(start, (int, float)) or not math.isfinite(start) or start < 0):
                raise MeetingError("Invalid transcript timestamp.")
            if end is not None and (not isinstance(end, (int, float)) or not math.isfinite(end) or (start is not None and end < start)):
                raise MeetingError("Invalid transcript timestamp.")
            # Bound segment sizes for reliable tool/Page pagination; timestamps stay attached.
            for offset in range(0, len(content), 1800):
                clean.append({"id": len(clean), "start": start, "end": end, "text": content[offset:offset + 1800]})
        if not clean:
            raise MeetingError("No speech/text was found. Check the recording before making notes.")
        revision = hashlib.sha256(json.dumps(clean, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        transcript = {"revision": revision, "engine": engine, "segments": clean, "created_at": now(), "speaker_labels": False}
        write_json(folder / "transcript.json", transcript)
        (folder / "transcript.md").write_text("# Full transcript\n\n" + "\n\n".join(transcript_lines(transcript)) + "\n", encoding="utf-8")
        return transcript

    def import_file(self, path, title=None, participants=None, meeting_date=None):
        source = Path(text(path, "path", 4096)).expanduser().resolve()
        if not source.is_file():
            raise MeetingError("Source file not found.")
        suffix = source.suffix.lower()
        if suffix not in {".txt", ".md", ".wav", ".m4a", ".mp3", ".mp4", ".aac", ".flac", ".aiff", ".ogg", ".webm", ".caf"}:
            raise MeetingError("Import a text/Markdown transcript or a supported audio/video file.")
        content = None
        if suffix in {".txt", ".md"}:
            content = source.read_text(encoding="utf-8").strip()
            if not content:
                raise MeetingError("Transcript file is empty.")
        folder, meta = self.create(title or source.stem, "import", participants, meeting_date)
        saved = folder / ("source" + suffix)
        shutil.copy2(source, saved)
        write_json(folder / "source.json", {"file": saved.name, "original_name": source.name})
        if content:
            self.put_transcript(folder, [{"text": part} for part in re.split(r"\n\s*\n", content)], "imported text (untimed)")
            write_json(folder / "transcribe-job.json", {"state": "ready"})
        return {"meeting": meta, "transcript_ready": bool(content), "next_step": "Read the transcript." if content else "Call transcribe_meeting."}

    def launch_job(self, meeting_id, phase, model="base", language=None):
        folder = self.folder(meeting_id)
        if model not in MODELS:
            raise MeetingError("Unsupported Whisper model.")
        if language is not None and (not isinstance(language, str) or not re.fullmatch(r"[a-z]{2,3}", language)):
            raise MeetingError("language must be a two/three-letter language code, or omitted for detection.")
        with self.lock():
            rec = self.recorder(folder)
            if rec["state"] in ACTIVE:
                raise MeetingError("Stop the recording and wait for finalization first.")
            job = self.job(folder, phase)
            if job["state"] in {"ready", "running"}:
                return {"meeting_id": meeting_id, **job}
            if phase == "prepare" and not self.status(meeting_id)["notes_current"]:
                raise MeetingError("Save notes for the current transcript before preparing publication.")
            if not shutil.which("ffmpeg"):
                raise MeetingError("ffmpeg is required for audio processing.")
            if phase == "transcribe" and not shutil.which("whisper"):
                raise MeetingError("Install openai-whisper to transcribe locally.")
            job = {"state": "running", "phase": phase, "started_at": now(), "started_epoch": time.time()}
            write_json(folder / f"{phase}-job.json", job)
            command = [sys.executable, str(ROOT / "server/main.py"), "--worker", phase, "--meeting", meeting_id,
                       "--home", str(self.home), "--model", model]
            if language:
                command += ["--language", language]
            try:
                with (folder / f"{phase}.log").open("a") as log:
                    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                # The worker owns this file after launch; don't overwrite its progress.
                job["pid"] = proc.pid
            except OSError as error:
                write_json(folder / f"{phase}-job.json", {**job, "state": "error", "error": str(error)})
                raise MeetingError("Could not start processing worker.") from error
        return {"meeting_id": meeting_id, **job, "next_step": "Poll meeting_status for completion."}

    def read_transcript(self, meeting_id, start_segment=0, limit=40):
        integer(start_segment, "start_segment", 0, 10**8)
        integer(limit, "limit", 1, 100)
        transcript = read_json(self.folder(meeting_id) / "transcript.json")
        if not transcript:
            raise MeetingError("Transcript is not ready.")
        selected, size = [], 0
        for segment in transcript["segments"][start_segment:start_segment + limit]:
            length = len(json.dumps(segment, ensure_ascii=False).encode())
            if selected and size + length > 14000:
                break
            selected.append(segment)
            size += length
        following = start_segment + len(selected)
        return {"meeting_id": meeting_id, "revision": transcript["revision"], "speaker_labels": False,
                "total_segments": len(transcript["segments"]), "segments": selected,
                "next_segment": following if following < len(transcript["segments"]) else None}

    def save_notes(self, meeting_id, transcript_revision, notes):
        folder = self.folder(meeting_id)
        with self.lock():
            transcript = read_json(folder / "transcript.json")
            if not transcript or transcript_revision != transcript["revision"]:
                raise MeetingError("Transcript revision changed or is missing. Read all of the current transcript first.")
            if not isinstance(notes, dict) or set(notes) != {"summary", "topics", "decisions", "actions", "open_questions", "uncertainties"}:
                raise MeetingError("notes must contain summary, topics, decisions, actions, open_questions, uncertainties.")
            ids = {s["id"] for s in transcript["segments"]}

            def point(item, action=False):
                fields = {"task", "owner", "due_date", "evidence"} if action else {"text", "evidence"}
                if not isinstance(item, dict) or set(item) != fields:
                    raise MeetingError(f"Each item must contain exactly {sorted(fields)}.")
                text(item["task" if action else "text"], "note item", 4000)
                evidence = item["evidence"]
                if not isinstance(evidence, list) or not evidence or any(type(i) is not int or i not in ids for i in evidence):
                    raise MeetingError("Every note item requires existing transcript segment IDs in evidence.")
                if action:
                    if item["owner"] is not None:
                        text(item["owner"], "owner", 200)
                    if item["due_date"] is not None:
                        try:
                            dt.date.fromisoformat(item["due_date"])
                        except (TypeError, ValueError):
                            raise MeetingError("due_date must be YYYY-MM-DD or null.")
            for key in ("summary", "decisions", "actions", "open_questions", "uncertainties"):
                if not isinstance(notes[key], list) or len(notes[key]) > 200:
                    raise MeetingError(f"{key} must be a list, up to 200 items.")
                for item in notes[key]:
                    point(item, key == "actions")
            if not notes["summary"]:
                raise MeetingError("Provide at least one summary item.")
            if not isinstance(notes["topics"], list) or len(notes["topics"]) > 100:
                raise MeetingError("topics must be a list, up to 100 topics.")
            for topic in notes["topics"]:
                if not isinstance(topic, dict) or set(topic) != {"topic", "points"}:
                    raise MeetingError("Each topic needs topic and points.")
                text(topic["topic"], "topic", 200)
                if not isinstance(topic["points"], list) or len(topic["points"]) > 200:
                    raise MeetingError("Topic points must be a list, up to 200 items.")
                for item in topic["points"]:
                    point(item)
            previous = read_json(folder / "notes.json")
            if previous:
                write_json(folder / "revisions" / f"notes-{time.time_ns()}.json", previous)
            result = {"transcript_revision": transcript_revision, "revision": uuid.uuid4().hex, "notes": notes, "saved_at": now()}
            write_json(folder / "notes.json", result)
            (folder / "notes.md").write_text(self.render_notes(folder), encoding="utf-8")
            # New notes invalidate a previously prepared/published version.
            write_json(folder / "prepare-job.json", {"state": "not_started"})
            pub = read_json(folder / "publication.json", {"assets": {}})
            pub["state"] = "pending"
            write_json(folder / "publication.json", pub)
        return {"meeting_id": meeting_id, "notes_revision": result["revision"], "saved_locally": True,
                "next_step": "Prepare and publish to Spaces; this is not yet a saved Spaces meeting."}

    def render_notes(self, folder):
        notes = read_json(folder / "notes.json")["notes"]
        transcript = read_json(folder / "transcript.json")
        segments = transcript["segments"]
        manifest = read_json(folder / "publication-manifest.json", {})
        receipts = read_json(folder / "publication.json", {"assets": {}})

        def evidence(item):
            references = []
            for i in dict.fromkeys(item["evidence"]):
                label = f"{stamp(segments[i]['start'])}, segment {i}"
                if manifest.get("transcript_revision") == transcript["revision"]:
                    asset = next((a for a in manifest.get("assets", []) if a["kind"] == "transcript" and i in a.get("segment_ids", [])), None)
                    receipt = receipts.get("assets", {}).get(asset["name"], {}) if asset else {}
                    if asset and receipt.get("sha256") == asset["sha256"]:
                        label = f"[{label}]({receipt['reference']})"
                references.append(label)
            return " (" + "; ".join(references) + ")"
        def bullet(item):
            return "- " + md_text(item["text"]) + evidence(item)
        lines = ["## Summary", "", *[bullet(i) for i in notes["summary"]]]
        if notes["topics"]:
            lines += ["", "## Discussion"]
            for topic in notes["topics"]:
                lines += ["", "### " + md_text(topic["topic"]), "", *[bullet(i) for i in topic["points"]]]
        for key, title in (("decisions", "Decisions"), ("actions", "Action items"), ("open_questions", "Open questions"), ("uncertainties", "Unclear details")):
            lines += ["", "## " + title, ""]
            if not notes[key]:
                lines.append("None identified in the transcript.")
            elif key == "actions":
                for item in notes[key]:
                    lines.append(f"- [ ] {md_text(item['task'])} — Owner: {md_text(item['owner'] or 'Not stated')}; due: {item['due_date'] or 'Not stated'}" + evidence(item))
            else:
                lines.extend(bullet(i) for i in notes[key])
        return "\n".join(lines) + "\n"

    def publication(self, meeting_id, start_chunk=0, limit=3):
        folder = self.folder(meeting_id)
        integer(start_chunk, "start_chunk", 0, 10**8)
        integer(limit, "limit", 1, 5)
        if self.job(folder, "prepare")["state"] != "ready":
            return self.launch_job(meeting_id, "prepare")
        manifest = read_json(folder / "publication-manifest.json")
        transcript = read_json(folder / "transcript.json")
        notes = read_json(folder / "notes.json")
        if (manifest["transcript_revision"], manifest["notes_revision"]) != (transcript["revision"], notes["revision"]):
            raise MeetingError("Publication preparation is outdated. Save notes and prepare again.")
        chunks = pack_chunks(transcript_lines(transcript))
        meta = read_json(folder / "meeting.json")
        following = start_chunk + min(limit, max(0, len(chunks) - start_chunk))
        with self.lock():
            receipts = read_json(folder / "publication.json", {"assets": {}})
            changed = False
            for asset in manifest["assets"]:
                saved = receipts.get("assets", {}).get(asset["name"], {})
                if saved.get("sha256") == asset["sha256"] and saved.get("confirmed_at") and saved.get("reference"):
                    # v1 could only persist an asset after verified=true and a matching digest.
                    # Restore those existing verification semantics; no new upload is asserted.
                    for key, value in {"file_access_confirmed": True, "byte_size": asset["byte_size"]}.items():
                        if key not in saved:
                            saved[key] = value
                            changed = True
            if changed:
                write_json(folder / "publication.json", receipts)
        return {"state": "ready", "meeting": meta, "destination": self.configuration(),
                "idempotency_key": "ai-meeting-notes-" + meeting_id, "page_title": f"{meta['meeting_date']} — {meta['title']}",
                "notes_markdown": self.render_notes(folder), "transcript_chunks": chunks[start_chunk:following],
                "total_transcript_chunks": len(chunks), "next_chunk": following if following < len(chunks) else None,
                "manifest": manifest, "receipts": receipts}

    def receipt(self, meeting_id, stage, page_id, page_url=None, asset_name=None, reference=None, sha256=None, verified=False, byte_size=None):
        folder = self.folder(meeting_id)
        if not isinstance(page_id, str) or not re.fullmatch(r"page_[A-Za-z0-9_-]+", page_id):
            raise MeetingError("Use the canonical Page ID returned by Pages.")
        if page_url is not None and page_url != f"https://chatgpt.com/space/{page_id}":
            raise MeetingError("page_url must be the canonical HTTPS URL for this Page ID.")
        with self.lock():
            pub = read_json(folder / "publication.json", {"state": "pending", "assets": {}})
            if pub.get("page_id") and pub["page_id"] != page_id:
                raise MeetingError("This meeting is already linked to another Page. Reuse it to avoid duplicates.")
            pub.update(page_id=page_id, page_url=page_url or pub.get("page_url"), updated_at=now())
            if stage == "page":
                pub["state"] = "page_created"
            elif stage == "asset":
                manifest = read_json(folder / "publication-manifest.json")
                asset = next((a for a in manifest["assets"] if a["name"] == asset_name), None) if manifest else None
                if not asset or asset["sha256"] != sha256:
                    raise MeetingError("Asset name/digest does not match prepared files.")
                if not isinstance(reference, str) or not re.fullmatch(r"(?:library-file|project-file):[A-Za-z0-9_-]+", reference):
                    raise MeetingError("Use the persistent file reference returned by the upload tool.")
                if verified is not True:
                    raise MeetingError("Confirm file_access_confirmed from the upload receipt before recording it.")
                if byte_size is not None and (type(byte_size) is not int or byte_size != asset["byte_size"]):
                    raise MeetingError("Upload byte_size does not match the prepared file.")
                pub["assets"][asset_name] = {"reference": reference, "sha256": sha256, "confirmed_at": now(),
                                             "file_access_confirmed": True, "byte_size": asset["byte_size"]}
                pub["state"] = "uploading"
            elif stage == "complete":
                manifest = read_json(folder / "publication-manifest.json")
                transcript = read_json(folder / "transcript.json")
                notes = read_json(folder / "notes.json")
                if not manifest or not transcript or not notes or (manifest["transcript_revision"], manifest["notes_revision"]) != (transcript["revision"], notes["revision"]):
                    raise MeetingError("Prepare the current transcript/notes first.")
                missing = [a["name"] for a in manifest["assets"] if pub["assets"].get(a["name"], {}).get("sha256") != a["sha256"]]
                if missing:
                    raise MeetingError("Missing confirmed asset uploads: " + ", ".join(missing))
                if verified is not True or not pub.get("page_url"):
                    raise MeetingError("Read back the Page, verify all content/links and parent indexing, then set verified=true.")
                pub.update(state="complete", completed_at=now(), transcript_revision=transcript["revision"], notes_revision=notes["revision"])
            else:
                raise MeetingError("stage must be page, asset, or complete.")
            write_json(folder / "publication.json", pub)
        return pub

    def list_meetings(self, offset=0, limit=30):
        integer(offset, "offset", 0, 10**8)
        integer(limit, "limit", 1, 100)
        paths = sorted(self.home.glob("*/meeting.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        items = [{"meeting": read_json(p), "publication": read_json(p.parent / "publication.json", {"state": "not_published"}),
                  "recording": self.recorder(p.parent)["state"]} for p in paths[offset:offset + limit]]
        return {"items": items, "next_offset": offset + limit if offset + limit < len(paths) else None,
                "scope": "local processing cache; use Pages to browse the canonical Meetings library"}
