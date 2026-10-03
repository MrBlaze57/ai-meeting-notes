# Validation

## Current public-source preparation

- Per-user destination settings replace account-specific Page IDs and machine-specific MCP paths.
- The transcript directory ZIP excludes local MCP servers, app references, native capture code, build products, and meeting data.
- The Mac source package excludes native binaries, build/cache directories, bytecode, and meeting data.
- Tests cover per-user configuration isolation, invalid destinations, and refusing destination changes during an active publication, alongside the existing recording/processing/receipt tests.

On September 30, 2026, `python3 -m unittest discover -s tests -v` passed all 47 tests. `./script/build_and_run.sh --build-only` and native code-sign verification passed. Both skills passed the skill validator, and both release ZIPs were generated. No recording was started. These checks do not establish directory publication, current-version installation, live recording, or iPhone behavior.

## Prior 0.2.0 evidence

Before public-source preparation, 42 automated tests and the native build/code-sign check passed. A synthetic spoken sample was transcribed locally. A test-account Spaces Page, full transcript, native notes, and uploaded recording/transcript attachments were read back; upload receipts confirmed access, sizes, and SHA-256 digests. Private Page links and account-specific validation records are excluded from public source.

The installed 0.2.0 native Stop handler was exercised using copied synthetic audio in its `--test-stop` branch, which never invokes capture APIs. It finalized audio, launched a detached worker, transcribed, wrote notes, and published without a second chat request. A cold retry completed in about 75 seconds and reused its existing Page and attachments. This verifies the synthetic finalization-to-publication path, not live audio shutdown.

The detector returned a live `needs_accessibility` status. Real call detection still needs a meeting test after permissions are enabled. Recording playback and rendered Page layout were not verified. New-version installation and public ChatGPT/iPhone operation remain unverified.

No microphone or system-audio recording was started during validation.

## Permission persistence changes (October 3, 2026)

All 51 tests passed, including unchanged-build identity preservation, retaining the selected certificate, preserving the previous app after signing failure, and rebuilding invalid signatures. The native build and strict code-sign verification passed. A repeated build of both the source and installed recorder reused the existing binary without changing its contents, inode, or modification time. The installed recorder was updated and its signature verified.

The installed recorder's prompt-free Accessibility query reported **not authorized**. Background detection was re-enabled and reported **needs_accessibility**, with no active recording. Background watcher startup now keeps its setup window hidden and never requests Accessibility permission; the user can open settings explicitly. Native UI inspection was unavailable because Computer Use failed, so window visibility was not independently observed.

No valid code-signing certificate was available. This Mac remains on ad hoc signing. Actual permission retention across detector restarts awaits a user grant and read-back verification. Permission retention across code updates requires stable certificate signing; the new signing option supports it, but a certificate-signed build has not been validated here. No live capture or live meeting detection was tested.
