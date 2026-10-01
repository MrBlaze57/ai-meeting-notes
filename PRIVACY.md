# Privacy

Effective September 30, 2026. Publisher: John Hernandez.

AI Meeting Notes has two editions. The public transcript plugin contains instructions executed by ChatGPT or Codex. It has no publisher-operated server, analytics, advertising, or independent collection of your transcripts. Pasted text and uploaded files are processed by the OpenAI host under your account's applicable terms, privacy policy, and data controls.

The Mac companion captures audio only after an explicit recording request or Start click. Online capture includes microphone and audio from all Mac apps; in-person capture uses the microphone. It saves no screen images or video. Optional meeting detection reads accessible call controls and window titles on your Mac without saving UI snapshots or browser history.

Recordings, transcripts, settings, processing logs, and publication receipts are stored in a private local cache. The default is `~/Library/Application Support/AI Meeting Notes/cache`; `MEETING_NOTES_HOME` can select another location. Whisper transcription runs locally. Its dependencies or model downloads may contact their own providers.

For automatic notes and Spaces publication, the Mac companion sends the transcript through your signed-in Codex CLI and uploads notes, full transcripts, and recordings through your connected Pages account. The publisher does not receive them. Spaces is your canonical meeting library. The transcript plugin saves there only when requested and when the host exposes the needed Pages capabilities. It does not alter sharing.

Local caches remain until you remove them; there is no automatic retention period. After stopping recordings and processing, you can delete the cache using your file manager. Removing a cache does not remove the published Page or its attachments. Manage published data, sharing, and deletion in your OpenAI account. The public repository and release ZIPs contain no user recordings, transcripts, destination IDs, or credentials.

For support, use [GitHub issues](https://github.com/MrBlaze57/ai-meeting-notes/issues). Do not include private transcripts, recordings, credentials, or personal information in a public issue. This notice describes this project's handling; OpenAI and dependency providers govern their own services.
