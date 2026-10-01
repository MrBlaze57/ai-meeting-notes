# AI Meeting Notes

Build a Codex plugin for macOS, with online and in-person audio capture. Codex Spaces is the canonical meeting library: the Meetings parent page contains dated child pages with notes, full transcripts, and uploaded recordings. Local files are processing/recovery cache, not the final destination.

Use Python's standard library for the local MCP server and worker. Use AppKit, AVFoundation, and ScreenCaptureKit for capture; save audio only. Keep recording explicit, visible, and independently stoppable. Do not start recording as part of builds or tests.

Run `python3 -m unittest discover -s tests -v` and `./script/build_and_run.sh --build-only` after relevant changes. Do not claim live capture, Spaces publication, or installation passed without verifying those specific outcomes. Preserve Page edits and persist publication receipts for safe retries.
