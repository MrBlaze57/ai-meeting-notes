import plistlib
from pathlib import Path
import sys

info = {
    "CFBundleExecutable": "MeetingRecorder", "CFBundleIdentifier": "com.johnhernandez.ai-meeting-notes.recorder",
    "CFBundleName": "MeetingRecorder", "CFBundleDisplayName": "AI Meeting Notes Recorder",
    "CFBundlePackageType": "APPL", "CFBundleVersion": "3", "CFBundleShortVersionString": "0.3.0",
    "LSMinimumSystemVersion": "15.0", "NSPrincipalClass": "NSApplication",
    "NSMicrophoneUsageDescription": "Record your voice and in-person meeting conversations when you start a recording.",
    "NSScreenCaptureUsageDescription": "Capture Mac system audio for online meeting recordings. Screen images and video are not saved.",
    "NSHighResolutionCapable": True,
}
Path(sys.argv[1]).write_bytes(plistlib.dumps(info))
