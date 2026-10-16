"""Exercise the real worker with small CLI fixtures, never a microphone or model."""
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/ai-meeting-notes/server"))
from core import MeetingError, Store, read_json, write_json
from worker import work


class TranscriptionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.store = Store(self.root / "cache")
        self.folder, meta = self.store.create("Fixture", "in_person")
        self.mid = meta["id"]
        with wave.open(str(self.folder / "audio.wav"), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0\0" * 160)
        self.audio_before = (self.folder / "audio.wav").read_bytes()
        for name in ("mlx_whisper", "whisper"):
            self.install_cli(name)
        self.env = {"PATH": str(self.bin), "MEETING_NOTES_TRANSCRIPTION_ENGINE": "auto",
                    "FIXTURE_CALLS": str(self.root / "calls.jsonl")}
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, self.env).start()
        patch("core.ROOT", self.root / "plugin").start()
        patch("platform.system", return_value="Darwin").start()
        patch("platform.machine", return_value="arm64").start()

    def install_cli(self, name):
        path = self.bin / name
        path.write_text("#!" + sys.executable + "\n" + '''
import json, os
from pathlib import Path
import sys
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["FIXTURE_CALLS"], "a") as stream:
    stream.write(json.dumps({"engine": name, "args": args}) + "\\n")
mlx = name == "mlx_whisper"
mode = os.environ.get("FIXTURE_MLX" if mlx else "FIXTURE_WHISPER", "ok")
if mode == "crash":
    sys.exit(7)
if mode == "missing":
    sys.exit(0)
flag = "--output-dir" if mlx else "--output_dir"
output = Path(args[args.index(flag) + 1])
assert args[args.index("--output-format" if mlx else "--output_format") + 1] == "json"
assert args[args.index("--model") + 1] == (os.environ.get("FIXTURE_EXPECTED_MODEL", "mlx-community/whisper-base-mlx") if mlx else "base")
if not mlx:
    assert args[args.index("--fp16") + 1] == "False"
segments = [{"id": 0, "start": 0.0, "end": 0.01, "text": "MLX speech" if mlx else "CPU speech"}]
if mode == "invalid":
    segments[0]["end"] = -1
if mode == "empty":
    segments = []
if mode == "repetition":
    segments = [{"id": i, "start": i * 5, "end": i * 5 + 5, "text": "So"} for i in range(30)]
if mode == "malformed":
    (output / "audio.json").write_text("not json")
else:
    (output / "audio.json").write_text(json.dumps({"text": "fixture", "segments": segments, "language": "en"}))
''')
        path.chmod(0o755)

    def calls(self):
        return [json.loads(line) for line in (self.root / "calls.jsonl").read_text().splitlines()]

    def test_apple_silicon_prefers_mlx_and_keeps_language_and_timestamps(self):
        work(self.store, self.mid, "transcribe", language="es")
        transcript = read_json(self.folder / "transcript.json")
        self.assertEqual(transcript["segments"], [{"id": 0, "start": 0.0, "end": 0.01, "text": "MLX speech"}])
        self.assertEqual(transcript["engine"], "MLX Whisper base")
        calls = self.calls()
        self.assertEqual([c["engine"] for c in calls], ["mlx_whisper"])
        args = calls[0]["args"]
        self.assertEqual(args[args.index("--condition-on-previous-text") + 1], "False")
        self.assertEqual(calls[0]["args"][-2:], ["--language", "es"])
        job = read_json(self.folder / "transcribe-job.json")
        self.assertEqual(job["engine"], "mlx")
        self.assertGreaterEqual(job["transcription_seconds"], 0)
        self.assertEqual((self.folder / "audio.wav").read_bytes(), self.audio_before)

    def test_mlx_failure_or_unusable_output_falls_back_without_stale_results(self):
        # Old output must never be accepted when a CLI exits zero without writing.
        write_json(self.folder / "whisper/audio.json", {"segments": [{"text": "stale result"}]})
        for mode in ("crash", "missing", "malformed", "invalid", "empty", "repetition"):
            with self.subTest(mode=mode), patch.dict(os.environ, {"FIXTURE_MLX": mode}):
                work(self.store, self.mid, "transcribe")
                transcript = read_json(self.folder / "transcript.json")
                self.assertEqual(transcript["segments"][0]["text"], "CPU speech")
                job = read_json(self.folder / "transcribe-job.json")
                self.assertEqual(job["engine"], "whisper")
                self.assertTrue(job["fallback_reason"])
                self.assertEqual(job["state"], "ready")

    def test_intel_or_explicit_cpu_selection_uses_current_whisper(self):
        for cpu, preference in (("x86_64", "auto"), ("arm64", "whisper")):
            with self.subTest(cpu=cpu, preference=preference), patch("platform.machine", return_value=cpu), patch.dict(os.environ, {"MEETING_NOTES_TRANSCRIPTION_ENGINE": preference}):
                work(self.store, self.mid, "transcribe")
                self.assertEqual(read_json(self.folder / "transcript.json")["engine"], "Whisper base")
        self.assertEqual([c["engine"] for c in self.calls()], ["whisper", "whisper"])

    def test_mlx_can_transcribe_without_cpu_dependency(self):
        (self.bin / "whisper").unlink()
        self.install_cli("ffmpeg")  # Dependency discovery only; normalized audio already exists.
        with patch("core.subprocess.Popen") as launch:
            # Keep worker launch outside this unit test; the actual CLI runs below.
            launch.return_value.pid = os.getpid()
            self.assertEqual(self.store.launch_job(self.mid, "transcribe")["state"], "running")
        work(self.store, self.mid, "transcribe")
        self.assertEqual(read_json(self.folder / "transcript.json")["engine"], "MLX Whisper base")

    def test_missing_mlx_installation_uses_cpu_whisper(self):
        (self.bin / "mlx_whisper").unlink()
        work(self.store, self.mid, "transcribe")
        self.assertEqual(read_json(self.folder / "transcript.json")["engine"], "Whisper base")

    def test_prefetched_safetensors_model_is_used_without_a_hub_lookup(self):
        model = self.root / "plugin/.build/mlx-models/base"
        model.mkdir(parents=True)
        (model / "config.json").write_text("{}")
        (model / "weights.safetensors").write_bytes(b"model fixture")
        with patch.dict(os.environ, {"FIXTURE_EXPECTED_MODEL": str(model)}):
            work(self.store, self.mid, "transcribe")
        self.assertEqual(read_json(self.folder / "transcript.json")["engine"], "MLX Whisper base")
        self.assertEqual(read_json(self.folder / "transcribe-job.json")["model_reference"], str(model))

    def test_idle_sleep_is_inhibited_only_for_the_engine_process(self):
        assertion = self.bin / "caffeinate"
        assertion.write_text("#!" + sys.executable + "\n" + '''
import json, os, sys
with open(os.environ["FIXTURE_CALLS"], "a") as stream:
    stream.write(json.dumps({"engine": "caffeinate", "args": sys.argv[1:]}) + "\\n")
assert sys.argv[1] == "-i"
os.execv(sys.argv[2], sys.argv[2:])
''')
        assertion.chmod(0o755)
        work(self.store, self.mid, "transcribe")
        self.assertEqual([c["engine"] for c in self.calls()], ["caffeinate", "mlx_whisper"])
        self.assertEqual(read_json(self.folder / "transcribe-job.json")["state"], "ready")

    def test_failed_engines_leave_existing_transcript_and_audio_unchanged(self):
        original = self.store.put_transcript(self.folder, [{"text": "Previously saved transcript"}], "saved")
        with patch.dict(os.environ, {"FIXTURE_MLX": "crash", "FIXTURE_WHISPER": "crash"}):
            with self.assertRaises(MeetingError):
                work(self.store, self.mid, "transcribe")
        self.assertEqual(read_json(self.folder / "transcript.json"), original)
        self.assertEqual((self.folder / "audio.wav").read_bytes(), self.audio_before)
        self.assertEqual(read_json(self.folder / "transcribe-job.json")["state"], "error")


if __name__ == "__main__":
    unittest.main()
