from pathlib import Path
import json
import subprocess
import tempfile
import unittest

BINARY = Path(__file__).resolve().parents[1] / "plugins/ai-meeting-notes/bin/MeetingRecorder.app/Contents/MacOS/MeetingRecorder"


@unittest.skipUnless(BINARY.is_file(), "Build the recorder to run native detection fixtures.")
class DetectionTest(unittest.TestCase):
    def run_steps(self, steps):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "steps.json"
            fixture.write_text(json.dumps(steps))
            result = subprocess.run([str(BINARY), "--test-detection", str(fixture)], capture_output=True, text=True, check=True)
            return json.loads(result.stdout)

    def step(self, snapshots, time=0, recording=False):
        return {"snapshots": snapshots, "time": time, "recording": recording}

    def zoom(self):
        return {"bundleID": "us.zoom.xos", "title": "Zoom Meeting", "controls": ["Mute", "Leave"]}

    def test_app_launch_is_not_a_meeting(self):
        home = {"bundleID": "us.zoom.xos", "title": "Zoom Workplace", "controls": ["New meeting", "Join", "Settings"]}
        self.assertEqual(self.run_steps([self.step([home], 0), self.step([home], 5)]), [None, None])

    def test_zoom_waits_for_two_scans_and_does_not_repeat(self):
        results = self.run_steps([self.step([self.zoom()], time) for time in [0, 5, 10, 35, 120]])
        self.assertIsNone(results[0])
        self.assertEqual(results[1]["provider"], "Zoom")
        self.assertEqual(results[2:], [None, None, None])

    def test_duplicate_windows_do_not_count_as_two_scans(self):
        results = self.run_steps([self.step([self.zoom(), self.zoom()])])
        self.assertEqual(results, [None])

    def test_meet_prejoin_does_not_prompt(self):
        lobby = {"bundleID": "com.google.Chrome", "title": "Meet", "url": "https://meet.google.com/abc-defg-hij", "controls": ["Turn off microphone", "Join now"]}
        self.assertEqual(self.run_steps([self.step([lobby], 0), self.step([lobby], 5)]), [None, None])

    def test_active_meet_prompts(self):
        call = {"bundleID": "com.google.Chrome", "title": "Weekly sync", "url": "https://meet.google.com/abc-defg-hij?authuser=1", "controls": ["Turn off microphone", "Leave call"]}
        results = self.run_steps([self.step([call], 0), self.step([call], 5)])
        self.assertEqual(results[1]["provider"], "Google Meet")
        self.assertEqual(results[1]["key"], "meet.google.com/abc-defg-hij")

    def test_random_webpage_with_call_labels_is_not_a_meeting(self):
        doc = {"bundleID": "com.google.Chrome", "title": "Call controls documentation", "url": "https://example.com/meeting", "controls": ["Mute", "Leave"]}
        self.assertEqual(self.run_steps([self.step([doc], 0), self.step([doc], 5)]), [None, None])

    def test_teams_active_call_prompts(self):
        call = {"bundleID": "com.microsoft.teams2", "title": "Operations sync", "controls": ["Mic", "Leave", "Camera"]}
        results = self.run_steps([self.step([call], 0), self.step([call], 5)])
        self.assertEqual(results[1]["provider"], "Microsoft Teams")

    def test_active_recording_suppresses_popup_after_manual_stop(self):
        results = self.run_steps([self.step([self.zoom()], 0, True), self.step([self.zoom()], 5, True), self.step([self.zoom()], 15)])
        self.assertEqual(results, [None, None, None])

    def test_brief_absence_does_not_reset_dismissal(self):
        results = self.run_steps([self.step([self.zoom()], 0), self.step([self.zoom()], 5), self.step([], 20), self.step([self.zoom()], 25), self.step([self.zoom()], 30)])
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_new_call_after_leave_can_prompt(self):
        results = self.run_steps([self.step([self.zoom()], 0), self.step([self.zoom()], 5), self.step([], 60), self.step([self.zoom()], 65), self.step([self.zoom()], 70)])
        self.assertEqual(sum(r is not None for r in results), 2)
