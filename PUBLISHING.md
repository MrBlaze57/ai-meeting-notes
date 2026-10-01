# Plugin distribution

This repository includes separately named plugins because the Mac edition depends on a local MCP server and Pages app reference. The public directory submission package is the skills-only transcript edition, matching the first iPhone release scope. OpenAI currently prohibits app references in submitted ZIPs and requires remote HTTPS for publicly reviewed MCP services. An MCP server cannot currently be added later to an existing skills-only directory plugin; a future hosted recording service would need a separate plugin identity.

## Prepared artifacts

Run `python3 scripts/package-plugin.py`:

- `dist/meeting-notes-transcripts.zip`: directory submission package. Portable and Codex manifests, transcript skill, icons, and MIT license. No MCP, app references, native binary, credentials, or user data.
- `dist/ai-meeting-notes.zip`: Mac companion source package for repository distribution. Build the native companion locally. This ZIP is not a public directory submission.

Both editions are discoverable through `.agents/plugins/marketplace.json`. A repository marketplace and GitHub release provide public source/package distribution, independent of directory review.

## Public directory

1. Open [OpenAI Plugins](https://platform.openai.com/plugins) in the organization/project that should own the listing.
2. Complete individual or business developer verification if needed. Select **Upload new or existing plugin** and the verified developer identity.
3. Upload **meeting-notes-transcripts.zip**. Review and fix automated metadata and skill findings.
4. Complete the required review flow and policy attestations. Submit the draft, track its status, and publish the approved version.
5. Verify the live listing and install it in ChatGPT on iPhone. Use the synthetic transcript below to check notes and platform capability limits. Do not claim mobile operation from ZIP validation alone.

Current submission status (October 1, 2026): the transcript edition 1.0.0 was submitted for review by the developer under the verified identity. Metadata and skill checks passed with no issues. The portal shows **In review** and **Not published**. Approval, public directory publication, and iPhone operation remain unverified. Once approved, publish the version and verify the resulting public listing before reporting directory availability.

### Original Mac edition

The original `ai-meeting-notes` 0.3.0 ZIP was also uploaded on October 1, 2026. The portal created an **AI Meeting Notes for Mac** draft and shows **Needs attention**, **Not published**, and MCP configuration **Unavailable**. It has not been submitted for review. The portal reports:

- The Pages integration reference in `.app.json` prevents submission. The directory requires submitting a supported MCP service directly.
- The local command MCP server requires approval from an OpenAI representative; the standard directory route uses an HTTPS MCP URL.
- The display name is too generic and needs a recognizable product or brand name.

These are directory eligibility issues, separate from local package validation. Removing the server and Pages dependency would not preserve the original recording and automatic-publication workflow. A directory edition needs a supported connector design that retains the Mac companion and each user's Spaces access, or an approved local-MCP route. The repository package remains available for local Codex installation. No recording was started during upload checks.

No publisher credentials or private meeting data belong in the ZIP. The listing points to this repo, public support issues, privacy notice, and software terms. Never upload private transcripts to public issues.

## Transcript check

Use a synthetic transcript dated September 29, 2026:

> [00:00] Maya: I propose launching Friday, but we need approval first.
> [00:10] Jo: Agreed, hold the launch until approval.
> [00:20] Maya: I'll send the draft tomorrow.
> [00:30] Jo: Someone should check the price. We haven't assigned that yet.
> [00:40] Maya: The vendor name is unclear in my notes.

Expected: hold launch pending approval is the decision; Friday remains a proposal. Maya owns sending the draft, due September 30. Checking the price has no assigned owner or deadline. Vendor identity remains unclear. Source timestamps are retained. A request to record on iPhone must explain the capture limitation. A Spaces save is only reported after actual host publication and verification; missing capabilities are disclosed.

References: [Package guide](https://developers.openai.com/plugins/build/plugins), [Submission](https://developers.openai.com/plugins/deploy/submission), [Plugin guidelines](https://developers.openai.com/plugins/plugin-guidelines).
