"""Write the two Codex project configuration files (run with required filesystem approval)."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
marketplace = {"name": "ai-meeting-notes-local", "interface": {"displayName": "AI Meeting Notes"},
               "plugins": [{"name": "ai-meeting-notes", "source": {"source": "local", "path": "./plugins/ai-meeting-notes"},
                            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}]}
marketplace["plugins"].append({"name": "meeting-notes-transcripts",
    "source": {"source": "local", "path": "./plugins/meeting-notes-transcripts"},
    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"})
market_path = root / ".agents/plugins/marketplace.json"
market_path.parent.mkdir(parents=True, exist_ok=True)
market_path.write_text(json.dumps(marketplace, indent=2) + "\n")
env_path = root / ".codex/environments/environment.toml"
env_path.parent.mkdir(parents=True, exist_ok=True)
env_path.write_text('version = 1\nname = "AI Meeting Notes"\n\n[setup]\nscript = ""\n\n[[actions]]\nname = "Run"\nicon = "run"\ncommand = "./script/build_and_run.sh"\n')
print("Configured repo marketplace and the Run action; no recording was started.")
