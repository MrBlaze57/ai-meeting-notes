# MLX transcription branch validation

Measured October 15, 2026 on an Apple M5 Mac with 24 GiB memory. These measurements cover local transcription, not notes generation or Spaces publication.

| Audio input | CPU Whisper | MLX Whisper |
| --- | ---: | ---: |
| Same 180-second meeting excerpt | 25.661 s | 3.091 s |
| Complete 2,968.994-second recording | Not rerun | 31.630 s |

The controlled excerpt was approximately **8.3 times faster** with MLX. This is one machine and recording, not a general speed guarantee. Both timing runs started a fresh CLI process and included model startup. The final comparison ran sequentially after builds and other benchmarks finished.

## Configuration

- CPU: `openai-whisper` 20250625, PyTorch 2.12.0, `base`, FP32, eight threads, default previous-text context.
- GPU: `mlx-whisper` 0.4.3, MLX 0.32.4, Metal on Apple M5, converted `base`, FP16, `--condition-on-previous-text False`.
- Model: `mlx-community/whisper-base-mlx`, revision `1e3e249fb8d01c655324bd6841b1deadffd6d04c`, prefetched locally.
- Language: automatic detection. No cloud transcription service was called.
- Input: existing mono 16 kHz PCM audio. The short excerpt used seconds 120–300 of the longer input.

## Method and limits

Each engine ran through the real worker in a separate private cache folder, with `MEETING_NOTES_TRANSCRIPTION_ENGINE` forced to that engine so fallback could not inflate the claimed MLX result. A separate check used `Store.import_file` and `Store.launch_job` to verify normalization, automatic MLX selection, the detached CLI entrypoint, and a persisted `ready` receipt. No test started recording or published a Page.

Original audio, transcript, notes, and publication-receipt hashes were unchanged after the full-recording benchmark. Benchmark audio and transcripts remain in the ignored local processing cache; they are not included in this repository.

This is **not an accuracy benchmark**. There is no human reference transcript. The short excerpt produced 675 words with CPU Whisper and 554 with MLX; the complete recording produced 6,256 words in its previously saved CPU transcript and 7,493 with the final MLX settings. Differences include wording, omissions, punctuation, numbers, and segmentation. The engines are not interchangeable word for word, and representative human accuracy review remains necessary before a production rollout.

Long-recording validation exposed a repetition loop with cross-window text conditioning. The delivered MLX configuration disables that conditioning and rejects severely repetitive output before accepting a transcript. Invalid or missing output, CLI failure, and detected repetition fall back to CPU in `auto` mode. These checks do not detect every possible transcription error.

The historical hours-long job also overlapped macOS sleep, so its elapsed time is not used as the controlled CPU comparison. Transcription now uses `caffeinate -i` while its CLI runs; this prevents idle sleep, allows display sleep, and releases the assertion when the process exits.

## Native app

The bundle includes the selected Capture Ring PNG and compiled ICNS resource. Native compilation, strict code-signature verification, and bundled-resource checks passed. The user visually confirmed the Dock icon. During branch validation, the original installed recorder was preserved.

## Approved local rollout

On October 15, 2026, the user approved rollout and [PR #1](https://github.com/MrBlaze57/ai-meeting-notes/pull/1) was merged into `main`. The installed plugin received the exact tested app bundle, an isolated MLX environment matching the tested package versions, and the prefetched base model. The native build checks reused the verified signature instead of recompiling the app.

The installed Python 3.9 worker successfully normalized and transcribed a copied 180-second excerpt using MLX, without fallback or a model download. This first run in the new environment took 12.027 seconds for transcription, including cold startup. It is a deployment smoke check, separate from the controlled comparison above. All 31 protected meeting files retained their prior content hashes or audio size/modification timestamps. A rollback backup was retained locally.

The user approved refreshing the updated app's Accessibility entry and completed the macOS authentication prompt. The detector then reported `watching` with `requires_accessibility: false`, including after a full detector restart. Inspection of the running recorder processes found one regular Dock launcher and one accessory background detector.

All 70 tests passed on the merged tree. No live recording or Spaces publication was initiated during rollout verification. Recording/progress windows still need confirmation during a real meeting; no human-scored transcript accuracy benchmark has been completed.
