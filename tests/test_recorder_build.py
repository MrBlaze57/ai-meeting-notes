import hashlib
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "plugins/ai-meeting-notes/scripts/build-recorder.py"
spec = importlib.util.spec_from_file_location("recorder_builder", SOURCE)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BuildTools:
    """Stand-ins for Swift and codesign; never launch an app or capture audio."""
    def __init__(self):
        self.compiles = 0
        self.signers = []
        self.fail_signing = False

    def run(self, args, check=False, **kwargs):
        code, stdout, stderr = 0, "", ""
        if args[:2] == ["swiftc", "-version"]:
            stdout = "Swift fixture version 1"
        elif args[0] == "swiftc":
            self.compiles += 1
            output = Path(args[args.index("-o") + 1])
            output.write_bytes(b"compiled:" + b"".join(Path(p).read_bytes() for p in args if p.endswith(".swift")))
        elif args[0] == "/usr/bin/codesign":
            bundle = Path(args[-1])
            binary = bundle / "Contents/MacOS/MeetingRecorder"
            seal = bundle / "Contents/signature-fixture"
            if "--force" in args:
                self.signers.append(args[args.index("--sign") + 1])
                if self.fail_signing:
                    code = 1
                else:
                    seal.write_text(hashlib.sha256(binary.read_bytes()).hexdigest())
            elif "--verify" in args:
                code = int(not seal.exists() or seal.read_text() != hashlib.sha256(binary.read_bytes()).hexdigest())
            else:
                stderr = "Signature=adhoc"
        else:
            Path(args[-1]).write_text("fixture plist")
        if code and check:
            raise subprocess.CalledProcessError(code, args)
        return subprocess.CompletedProcess(args, code, stdout, stderr)


class RecorderBuildTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("native/MeetingRecorder.swift", "native/MeetingDetector.swift", "scripts/write-plist.py", "scripts/build-recorder.py"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        self.tools = BuildTools()

    def build(self, identity=None):
        return builder.build(self.root, identity, self.tools.run)

    def test_unchanged_build_preserves_binary_and_signature(self):
        bundle, changed = self.build()
        binary = bundle / "Contents/MacOS/MeetingRecorder"
        before = binary.stat().st_ino, binary.stat().st_mtime_ns, binary.read_bytes()
        self.assertTrue(changed)
        self.assertEqual(self.build(), (bundle, False))
        self.assertEqual(before, (binary.stat().st_ino, binary.stat().st_mtime_ns, binary.read_bytes()))
        self.assertEqual(self.tools.compiles, 1)
        self.assertEqual(self.tools.signers, ["-"])

    def test_certificate_is_remembered_when_environment_selection_is_absent(self):
        self.build("Apple Development fixture")
        (self.root / "native/MeetingRecorder.swift").write_text("changed source")
        self.assertTrue(self.build()[1])
        self.assertEqual(self.tools.signers, ["Apple Development fixture"] * 2)

    def test_failed_update_preserves_previous_signed_app(self):
        bundle, _ = self.build("Apple Development fixture")
        binary = bundle / "Contents/MacOS/MeetingRecorder"
        before = binary.read_bytes()
        (self.root / "native/MeetingRecorder.swift").write_text("changed source")
        self.tools.fail_signing = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.build()
        self.assertEqual(binary.read_bytes(), before)

    def test_invalid_signature_is_rebuilt_instead_of_reused(self):
        bundle, _ = self.build()
        (bundle / "Contents/MacOS/MeetingRecorder").write_text("tampered binary")
        self.assertTrue(self.build()[1])
        self.assertEqual(self.tools.compiles, 2)


if __name__ == "__main__":
    unittest.main()
