"""Reuse unchanged signed bundles; never change signing identity implicitly."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

IDENTIFIER = "com.johnhernandez.ai-meeting-notes.recorder"


def build(root, identity=None, run=subprocess.run):
    root = Path(root)
    bundle = root / "bin/MeetingRecorder.app"
    receipt_path = root / ".build/recorder-build.json"
    receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
    # An explicit certificate selection survives subsequent rebuilds. Never
    # replace it with ad hoc signing just because an environment variable is absent.
    signer = identity or receipt.get("signer") or "-"
    version = run(["swiftc", "-version"], capture_output=True, text=True, check=True).stdout
    target = platform.machine() + "-apple-macos15.0"
    inputs = [root / "native/MeetingRecorder.swift", root / "native/MeetingDetector.swift",
              root / "scripts/write-plist.py", root / "scripts/build-recorder.py"]
    digest = hashlib.sha256(json.dumps([version, target, signer]).encode())
    for path in inputs:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    fingerprint = digest.hexdigest()
    if receipt.get("fingerprint") == fingerprint and bundle.is_dir():
        valid = run(["/usr/bin/codesign", "--verify", "--strict", str(bundle)],
                    capture_output=True, text=True).returncode == 0
        if valid:
            return bundle, False

    if signer == "-" and bundle.is_dir() and not receipt:
        existing = run(["/usr/bin/codesign", "-dv", str(bundle)], capture_output=True, text=True)
        if existing.returncode == 0 and "Signature=adhoc" not in existing.stderr:
            raise RuntimeError("Existing recorder uses certificate signing. Set MEETING_NOTES_SIGNING_IDENTITY before rebuilding; refusing to replace it with ad hoc signing.")

    bundle.parent.mkdir(parents=True, exist_ok=True)
    # Marketplace installs may copy build caches from another absolute path.
    # Clang PCM files cannot be reused after that move.
    location = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
    module_cache = root / ".build/module-cache" / location
    module_cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".recorder-build-", dir=bundle.parent) as temp:
        candidate = Path(temp) / "MeetingRecorder.app"
        executable = candidate / "Contents/MacOS/MeetingRecorder"
        executable.parent.mkdir(parents=True)
        run(["swiftc", "-swift-version", "5", "-parse-as-library", "-O", "-target", target,
             "-module-cache-path", str(module_cache), str(inputs[0]), str(inputs[1]), "-o", str(executable)], check=True)
        run([sys.executable, str(root / "scripts/write-plist.py"), str(candidate / "Contents/Info.plist")], check=True)
        run(["/usr/bin/codesign", "--force", "--sign", signer, "--identifier", IDENTIFIER, str(candidate)], check=True)
        run(["/usr/bin/codesign", "--verify", "--strict", str(candidate)], check=True)
        # Keep the existing app if compilation or signing fails. Candidate code
        # is verified before replacing any installed build.
        backup = Path(temp) / "previous.app"
        if bundle.exists():
            bundle.rename(backup)
        try:
            candidate.rename(bundle)
        except BaseException:
            if backup.exists():
                backup.rename(bundle)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    receipt_path.write_text(json.dumps({"fingerprint": fingerprint, "signer": signer}, indent=2) + "\n")
    return bundle, True


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    bundle, changed = build(root, os.environ.get("MEETING_NOTES_SIGNING_IDENTITY"))
    print(("Built " if changed else "Reused unchanged signed recorder: ") + str(bundle))
    if changed and json.loads((root / ".build/recorder-build.json").read_text())["signer"] == "-":
        print("Local ad hoc build: permission grants can persist across restarts, but code changes may require reauthorization. Use MEETING_NOTES_SIGNING_IDENTITY for certificate-signed updates.")
