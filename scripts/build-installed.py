"""Build the desktop companion after a source-only repository marketplace install."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--marketplace", default="ai-meeting-notes-local")
    args = parser.parse_args()
    version = json.loads((ROOT / "plugins/ai-meeting-notes/plugin.json").read_text())["version"]
    installed = Path.home() / ".codex/plugins/cache" / args.marketplace / "ai-meeting-notes" / version
    build = installed / "scripts/build-recorder"
    if not build.is_file():
        parser.error("Install the desktop plugin first; its build script was not found at " + str(build))
    if subprocess.run(["pgrep", "-x", "MeetingRecorder"], capture_output=True).returncode == 0:
        parser.error("Close the idle recorder or stop the active meeting before rebuilding.")
    subprocess.run([str(build)], check=True)
    print("Built the installed companion; recording was not started.")
