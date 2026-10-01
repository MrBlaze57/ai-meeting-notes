"""Smoke-test the installed MCP server. Launches detector setup, never audio capture."""
import json
import os
from pathlib import Path
import subprocess
import time

root = Path(__file__).resolve().parents[1]
version = json.loads((root / "plugins/ai-meeting-notes/plugin.json").read_text())["version"]
installed = Path.home() / ".codex/plugins/cache/ai-meeting-notes-local/ai-meeting-notes" / version
server = json.loads((installed / ".mcp.json").read_text())["mcpServers"]["meeting-notes"]
env = dict(os.environ, **server.get("env", {}))
started = time.time()
messages = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "meeting-notes-smoke", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "meeting_status", "arguments": {}}},
]
run = subprocess.run([str(installed / server["command"]), *server["args"]], cwd=installed, env=env,
                     input="\n".join(json.dumps(m) for m in messages) + "\n", text=True, capture_output=True, timeout=30, check=True)
responses = {r["id"]: r for r in (json.loads(line) for line in run.stdout.splitlines())}
assert responses[1]["result"]["serverInfo"]["name"] == "ai-meeting-notes"
assert len(responses[2]["result"]["tools"]) == 13
assert responses[3]["result"]["isError"] is False
status = json.loads(responses[3]["result"]["content"][0]["text"])
assert status["active_recordings"] == []
assert status["destination"].get("meetings_page_id"), "Configure your own Meetings destination first."
watcher = Path(status["cache_path"]) / "watcher.json"
detector = {"state": "not_started"}
live = False
for _ in range(20):
    if watcher.exists():
        detector = json.loads(watcher.read_text())
        try:
            pid = detector.get("pid", 0)
            live = type(pid) is int and pid > 0
            if live:
                os.kill(pid, 0)
        except (ProcessLookupError, PermissionError):
            live = False
        if live and detector.get("heartbeat", 0) >= started - 1:
            break
    time.sleep(0.5)
assert live and detector.get("heartbeat", 0) >= started - 1, "Detector has not returned a fresh live status."
result = {"installed_path": str(installed), "mcp_initialized": True, "tool_count": 13,
          "active_recordings": 0, "destination": status["destination"],
          "detector": detector,
          "live_capture_tested": False, "live_call_detection_tested": False}
Path(status["cache_path"], "installed-validation.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
