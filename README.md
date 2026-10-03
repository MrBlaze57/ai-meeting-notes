# AI Meeting Notes

Open source meeting notes for ChatGPT and Codex, with a Mac recording companion.

| Edition | What it does | Platforms |
| --- | --- | --- |
| **AI Meeting Notes** (`meeting-notes-transcripts`) | Provided transcripts → summary, decisions, actions, open questions, and source references | ChatGPT and Codex; intended for ChatGPT on iPhone where plugins are available |
| **AI Meeting Notes for Mac** (`ai-meeting-notes`) | Explicit audio capture → local transcription → notes, full transcript, and recordings in Spaces | macOS 15+ with local dependencies |

The transcript edition is a skills-only package for the public plugin directory. Version 1.0.0 passed metadata and skill checks and was submitted by the developer; the portal shows **In review** and **Not published** as of October 1, 2026. A public GitHub repository or release ZIP does not mean it is already approved or listed in ChatGPT. Directory publication and an actual iPhone installation still need verification. It does not record on iPhone. Spaces saves require the host to expose Pages capabilities; otherwise notes are returned in chat with the missing save clearly reported.

The original Mac edition 0.3.0 was also uploaded as a directory draft. It shows **Needs attention** and has not been submitted for review: the portal flags its Pages integration reference, local command MCP server, and generic display name. See [PUBLISHING.md](PUBLISHING.md) for the requirements. The Mac edition is publicly distributed through the repository marketplace.

## Transcript notes

Paste a transcript or attach a readable text/Markdown transcript, then ask:

- “Turn this transcript into meeting notes.”
- “Extract the decisions and action items.”
- “Save these meeting notes to my Meetings page.”

The skill reads the full supplied source, separates proposals from decisions, and keeps unknown owners and deadlines unknown. It does not act on instructions spoken in the transcript. Saving to Spaces is verified before reporting success; existing Page edits are preserved on retries.

In Codex, install from the repository marketplace:

```sh
codex plugin marketplace add MrBlaze57/ai-meeting-notes
codex plugin add meeting-notes-transcripts@ai-meeting-notes-local
```

For ChatGPT and iPhone, install the public directory listing once it is published. Repository marketplace installation is a Codex distribution route, not a ChatGPT mobile installation route. See [PUBLISHING.md](PUBLISHING.md) for the prepared ZIP and submission process.

## Mac recorder: build and install

Requires macOS 15+, Swift command-line tools, Python 3.11+, ffmpeg/ffprobe, `openai-whisper`, and a signed-in Codex CLI with Pages connected. Whisper and ffmpeg are separate dependencies; the local MCP server and worker use Python's standard library. An uncached Whisper model requires a download.

```sh
git clone https://github.com/MrBlaze57/ai-meeting-notes.git
cd ai-meeting-notes
./script/build_and_run.sh --build-only
python3 -m unittest discover -s tests -v
codex plugin marketplace add .
codex plugin add ai-meeting-notes@ai-meeting-notes-local
```

Build before adding the local marketplace so the installed copy includes the recorder built for your Mac. Release ZIPs contain source, not signed/notarized binaries. If installing the desktop plugin directly from the GitHub marketplace, build its installed copy using `scripts/build-installed.py` after installation. Start a fresh Codex chat to load the skill and tools.

Ask Codex to set up your **Meetings** destination. It finds or creates your own parent Page, verifies access, and stores its returned ID with `configure_meetings_page`. No shared destination is shipped. The private default cache is `~/Library/Application Support/AI Meeting Notes/cache`; `MEETING_NOTES_HOME` can select another location. Settings and publication receipts survive plugin updates.

- “Start recording my online meeting.”
- “Start recording this in-person meeting.”
- “Stop recording and save the meeting to Spaces.”
- “Turn this recording into meeting notes and put it in Meetings.”
- “Enable meeting detection.” / “Disable meeting detection.”

Recording opens a visible window and red menu bar indicator. **Stop recording** stops capture and launches an independent background workflow: local transcription, Codex notes, then Spaces publication. No second chat message is needed. The window shows progress, **Open notes**, or **Retry** to resume checkpoints. Closing the progress window or chat does not cancel processing. The default duration limit is three hours, configurable up to eight hours.

Online capture includes your microphone and audio from all Mac apps. In-person capture uses the microphone. Grant microphone permission and, for online meetings, Screen & System Audio Recording access to **MeetingRecorder** when prompted. Only audio is saved.

## Detect a meeting and ask to record

The optional detector requires macOS Accessibility. Enable **MeetingRecorder** in **System Settings → Privacy & Security → Accessibility**. It looks for active Zoom, Teams, or Google Meet call controls and offers **Start recording**, **Not now**, and **Disable detection**. Audio starts only after Start is clicked. Detection uses English Accessibility labels and selected accessible windows; app/browser changes and inaccessible or inactive tabs can require manual recording. There is no login-item installation, stored browser history, or saved UI snapshot.

Background detection checks existing authorization without opening setup windows or requesting permission. Its menu bar settings provide an explicit **Enable Accessibility** action. macOS stores the grant; the plugin cannot grant itself permission. Grant access to the installed recorder copy that Codex actually launches.

Builds reuse the existing verified bundle when sources, compiler, target, and signing identity are unchanged. This preserves the app's identity during ordinary rebuild commands and restarts. Code changes in an ad hoc build can still require authorization again. For permission continuity across updates, sign with your Apple Development or Developer ID certificate:

```sh
MEETING_NOTES_SIGNING_IDENTITY='your certificate name or SHA-1' ./script/build_and_run.sh --build-only
```

The selected signer is remembered locally for later builds. Signing failures preserve the previous recorder; the build never silently falls back from certificate signing to ad hoc signing. `MeetingRecorder.app/Contents/MacOS/MeetingRecorder --check-accessibility` reports authorization without requesting access or starting recording.

## Canonical meeting library

The Mac companion saves dated child Pages beneath **Meetings**, with editable notes, the full transcript, a transcript attachment, and recording attachments. Source timestamps/segment references support substantive items. Whisper provides timestamps but no speaker identification. Recording files are split into upload-safe MP3 parts when necessary; current Pages tools do not provide an inline audio player.

Spaces is the final library. Local audio and processing checkpoints remain as recovery cache. After Stop, the signed-in Codex CLI generates notes and publishes through Pages. This shares the transcript with your OpenAI host and saves the meeting in your account. No separate OpenAI API key or publisher-operated server is used. If login expires, sign in with `codex login` and click Retry.

## Development and validation

```sh
python3 -m unittest discover -s tests -v
./script/build_and_run.sh --build-only
python3 scripts/package-plugin.py
```

`./script/build_and_run.sh` opens an idle setup window without recording. Builds refuse to overwrite an open recorder. Tests use fixtures and synthetic audio, never live capture. `python3 scripts/demo-pipeline.py` synthesizes speech for an optional local pipeline demo.

Publication receipts distinguish Page creation, uploads, content, indexing, and read-back verification. Retries reuse saved receipts and preserve Page edits. The automatic runner restricts processing to its assigned meeting and uses the connected Pages capabilities; it has no copied Spaces credentials or undocumented API integration.

See [VALIDATION.md](VALIDATION.md) for verified outcomes and limits. Live audio capture and actual online-call detection remain unverified; synthetic tests and builds do not prove them. ChatGPT directory approval and iPhone operation are separate outcomes.

[MIT License](LICENSE) · [Privacy](PRIVACY.md) · [Use](TERMS.md) · [Support](https://github.com/MrBlaze57/ai-meeting-notes/issues)
