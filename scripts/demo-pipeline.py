"""Generate synthetic speech, transcribe it locally, and prepare a reviewable sample."""
from pathlib import Path
import json
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "plugins/ai-meeting-notes/server"))
from core import Store
from worker import work

cache = root / ".meeting-cache"
cache.mkdir(exist_ok=True)
fixture = cache / "synthetic-meeting.aiff"
speech = ("This is a synthetic meeting for testing AI Meeting Notes. "
          "We agreed to move the launch to Friday. Maya will send the draft by Thursday. "
          "Jordan will check the vendor pricing. We do not have a deadline for the pricing check. "
          "The open question is whether the vendor can support the new launch date.")
if not fixture.exists():
    subprocess.run(["/usr/bin/say", "-o", str(fixture), speech], check=True)
probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(fixture)]))
if float(probe.get("format", {}).get("duration", 0)) <= 1:
    raise RuntimeError("Speech synthesis did not produce audio. Run the synthesis command outside a sandbox, then retry; no microphone is required.")
store = Store(cache)
meeting = store.import_file(str(fixture), title="Demo meeting — synthetic audio")
mid = meeting["meeting"]["id"]
(cache / "demo-meeting.json").write_text(json.dumps({"meeting_id": mid, "synthetic_source": speech}, indent=2))
print("Imported synthetic meeting " + mid, flush=True)
work(store, mid, "transcribe", "base", "en")
print(json.dumps(store.read_transcript(mid), ensure_ascii=False, indent=2))
