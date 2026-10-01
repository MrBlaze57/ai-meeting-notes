#!/bin/bash
set -euo pipefail
TASK_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASK_PLUGIN_ROOT="$TASK_PROJECT_ROOT/plugins/ai-meeting-notes"
MODE="${1:-run}"
case "$MODE" in
  run|--verify|--build-only) ;;
  *) echo "Usage: $0 [--build-only|--verify]" >&2; exit 2 ;;
esac
# Never kill an active meeting to rebuild its capture process.
if pgrep -x MeetingRecorder >/dev/null; then
  echo "Close the idle recorder or stop the active meeting before rebuilding." >&2
  exit 1
fi
"$TASK_PLUGIN_ROOT/scripts/build-recorder"
if [[ "$MODE" == "--build-only" ]]; then exit 0; fi
/usr/bin/open -n "$TASK_PLUGIN_ROOT/bin/MeetingRecorder.app"
if [[ "$MODE" == "--verify" ]]; then
  sleep 1
  pgrep -x MeetingRecorder >/dev/null
  echo "Recorder setup window launched; recording has not started."
fi
