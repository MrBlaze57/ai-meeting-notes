"""Invoke the same local tools from a JSON file for diagnostics; no recording by default."""
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "plugins/ai-meeting-notes/server"))
from core import Store
from main import dispatch

if len(sys.argv) not in {2, 3}:
    raise SystemExit("Usage: python3 scripts/invoke-tool.py TOOL [arguments.json]")
args = (json.load(sys.stdin) if sys.argv[2] == "-" else json.loads(Path(sys.argv[2]).read_text())) if len(sys.argv) == 3 else {}
print(json.dumps(dispatch(Store(root / ".meeting-cache"), sys.argv[1], args), ensure_ascii=False, indent=2))
