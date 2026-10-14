import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("swiftc"), "macOS and Swift are required for native microphone-level fixtures.")
class RecordingUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.binary = Path(cls.temp.name) / "meter-fixture"
        result = subprocess.run(["swiftc", "-swift-version", "5", "-parse-as-library", "-O", "-module-cache-path", str(Path(cls.temp.name) / "modules"),
                        str(ROOT / "plugins/ai-meeting-notes/native/RecordingUI.swift"),
                        str(ROOT / "tests/RecordingUIFixture.swift"), "-o", str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)

    def level(self, samples, **options):
        result = subprocess.run([str(self.binary)], input=json.dumps({"samples": samples, **options}), capture_output=True, text=True, check=True)
        return json.loads(result.stdout)["level"]

    def test_silence_and_empty_buffers_show_no_microphone_activity(self):
        self.assertEqual(self.level([[0, 0, 0]]), 0)
        self.assertEqual(self.level([[]]), 0)

    def test_microphone_waveform_increases_with_real_sample_energy(self):
        quiet = self.level([[0.01, -0.01]])
        loud = self.level([[0.5, -0.5]])
        self.assertAlmostEqual(quiet, 1 / 3, places=4)
        self.assertGreater(loud, quiet)
        self.assertAlmostEqual(self.level([[1, -1]]), 1, places=4)

    def test_interleaved_and_planar_stereo_have_the_same_energy(self):
        samples = [[0.5, -0.5], [0, 0]]
        self.assertAlmostEqual(self.level(samples), self.level(samples, interleaved=True), places=5)
        self.assertLess(self.level(samples), self.level([[0.5, -0.5]]))

    def test_integer_audio_formats_match_float_levels(self):
        samples = [[0.5, -0.5]]
        expected = self.level(samples)
        for kind in ("int16", "int32"):
            with self.subTest(format=kind):
                self.assertAlmostEqual(self.level(samples, format=kind), expected, places=4)

    def test_invalid_samples_do_not_show_activity_or_nan(self):
        self.assertEqual(self.level([[0, 0]], invalid_samples=True), 0)

    def test_missing_microphone_buffers_decay_to_silence(self):
        self.assertGreater(self.level([[0.5]], poll_at=10.1), 0)
        self.assertEqual(self.level([[0.5]], poll_at=10.6), 0)

    def test_stopping_resets_microphone_activity(self):
        self.assertEqual(self.level([[0.5]], reset=True), 0)


if __name__ == "__main__":
    unittest.main()
