import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/ai-meeting-notes/server"))
from main import main


class ManualRecordingTest(unittest.TestCase):
    def test_manual_button_launch_preserves_selected_capture_mode(self):
        for mode in ("online", "in_person"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                binary = root / "bin/MeetingRecorder.app/Contents/MacOS/MeetingRecorder"
                binary.parent.mkdir(parents=True)
                binary.touch()
                home = root / "cache"
                output = io.StringIO()
                # Exercise the CLI and real Store; only the external app launch is replaced.
                with patch("core.ROOT", root), patch("core.sys.platform", "darwin"), \
                     patch("core.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as launch, \
                     patch.object(sys, "argv", ["main.py", "--manual-start", "Weekly sync", "--mode", mode, "--home", str(home)]), \
                     contextlib.redirect_stdout(output):
                    main()
                result = json.loads(output.getvalue())
                self.assertEqual(result["meeting"]["mode"], mode)
                self.assertEqual(result["meeting"]["title"], "Weekly sync")
                command = launch.call_args.args[0]
                self.assertEqual(command[command.index("--mode") + 1], mode)
                self.assertTrue((home / result["meeting"]["id"] / "recorder.json").exists())

    def test_invalid_manual_capture_mode_never_launches_recorder(self):
        with tempfile.TemporaryDirectory() as temp, patch("core.subprocess.run") as launch, \
             patch.object(sys, "argv", ["main.py", "--manual-start", "Meeting", "--mode", "invalid", "--home", temp]), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main()
            launch.assert_not_called()
            self.assertEqual(list(Path(temp).glob("*/meeting.json")), [])


if __name__ == "__main__":
    unittest.main()
