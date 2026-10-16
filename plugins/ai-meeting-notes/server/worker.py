from __future__ import annotations

from collections import Counter
import json
import math
import os
import re
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time

from core import MeetingError, UPLOAD_LIMIT, digest, now, pack_chunks, read_json, transcript_lines, transcription_options, write_json


def run(command, keep_awake=False):
    assertion = shutil.which("caffeinate") if keep_awake and platform.system() == "Darwin" else None
    if assertion:
        # This assertion ends with the CLI process and permits display sleep.
        command = [assertion, "-i", *command]
    subprocess.run(command, check=True, stdin=subprocess.DEVNULL)


def normalize(store, folder):
    target = folder / "audio.wav"
    if target.is_file() and target.stat().st_size > 44:
        return target
    source = read_json(folder / "source.json")
    base = [shutil.which("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    if source:
        if Path(source["file"]).suffix in {".md", ".txt"}:
            return None
        args = ["-i", str(folder / source["file"]), "-vn", "-ac", "1", "-ar", "16000"]
    else:
        rec = store.recorder(folder)
        tracks = [(name, value) for name, value in rec.get("tracks", {}).items() if value.get("frames", 0) > 0 and (folder / value["file"]).is_file()]
        if not tracks:
            raise MeetingError("No usable recorded audio exists. Check microphone/system audio permissions.")
        if read_json(folder / "meeting.json")["mode"] == "online" and {n for n, _ in tracks} != {"microphone", "system"}:
            raise MeetingError("The online recording is missing microphone or system audio. Retained tracks can be inspected in the cache; don't report a complete online recording.")
        args, filters = [], []
        for i, (_, track) in enumerate(tracks):
            args += ["-i", str(folder / track["file"])]
            delay = round(max(0, track.get("start_seconds", 0)) * 1000)
            filters.append(f"[{i}:a]aresample=16000,aformat=channel_layouts=mono,adelay={delay}:all=1[t{i}]")
        inputs = "".join(f"[t{i}]" for i in range(len(tracks)))
        if len(tracks) > 1:
            filters.append(inputs + f"amix=inputs={len(tracks)}:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95[out]")
        else:
            filters.append(inputs + "anull[out]")
        args += ["-filter_complex", ";".join(filters), "-map", "[out]", "-ac", "1", "-ar", "16000"]
    temp = folder / "audio-pending.wav"
    run(base + args + [str(temp)])
    probe = json.loads(subprocess.check_output([shutil.which("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(temp)]))
    duration = float(probe.get("format", {}).get("duration", 0))
    if temp.stat().st_size <= 44 or not math.isfinite(duration) or duration <= 0:
        raise MeetingError("The audio file contains no samples.")
    os.replace(temp, target)
    return target


def transcribe(store, folder, model, language):
    started = time.monotonic()
    audio = normalize(store, folder)
    normalized_seconds = time.monotonic() - started
    if audio is None:
        if not (folder / "transcript.json").is_file():
            raise MeetingError("Imported transcript is missing.")
        return {"engine": "imported text"}
    options = transcription_options(model)
    if not options:
        raise MeetingError("No local transcription engine is available. Install openai-whisper or set up MLX Whisper.")
    attempts = []
    decoding_started = time.monotonic()
    for index, option in enumerate(options):
        attempt_started = time.monotonic()
        attempt = {"engine": option["engine"]}
        try:
            # A fresh output directory prevents zero-exit CLI failures from reusing
            # an older/partial transcript. MLX's CLI can report errors with exit 0.
            with tempfile.TemporaryDirectory(prefix=".transcribe-", dir=folder) as output:
                mlx = option["engine"] == "mlx"
                cmd = [option["binary"], str(audio), "--model", option["model"],
                       "--output-dir" if mlx else "--output_dir", output,
                       "--output-format" if mlx else "--output_format", "json", "--verbose", "False"]
                if mlx:
                    # Avoid carrying a misheard phrase through the remainder of
                    # a long meeting. Each audio window still supplies context.
                    cmd += ["--condition-on-previous-text", "False"]
                else:
                    cmd += ["--fp16", "False", "--threads", str(min(8, os.cpu_count() or 4))]
                if language:
                    cmd += ["--language", language]
                run(cmd, keep_awake=True)
                raw = read_json(Path(output) / "audio.json")
                if not isinstance(raw, dict) or not isinstance(raw.get("segments"), list):
                    raise MeetingError("The engine finished without a usable transcript file.")
                if mlx and len(raw["segments"]) >= 20:
                    repeated = Counter(" ".join(s.get("text", "").lower().split()) for s in raw["segments"])
                    span = max(s.get("end", 0) for s in raw["segments"]) - min(s.get("start", 0) for s in raw["segments"])
                    if span >= 60 and repeated.most_common(1)[0][1] >= max(20, len(raw["segments"]) / 2):
                        raise MeetingError("MLX output contains excessive repeated segments.")
                store.put_transcript(folder, raw["segments"], ("MLX Whisper " if mlx else "Whisper ") + model)
                write_json(folder / "whisper/audio.json", raw)
            attempt["seconds"] = round(time.monotonic() - attempt_started, 3)
            attempts.append(attempt)
            return {"engine": option["engine"], "model": model, "model_reference": option["model"],
                    "normalization_seconds": round(normalized_seconds, 3),
                    "transcription_seconds": round(time.monotonic() - decoding_started, 3),
                    "attempts": attempts, "fallback_reason": attempts[0].get("error")}
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError, MeetingError) as error:
            attempt.update(seconds=round(time.monotonic() - attempt_started, 3), error=str(error)[:500])
            attempts.append(attempt)
            if index + 1 < len(options):
                print(f"Local {option['engine']} transcription failed; trying the next available engine.", flush=True)
    raise MeetingError("Local transcription failed. " + "; ".join(a["engine"] + ": " + a["error"] for a in attempts))


def prepare(store, folder):
    transcript = read_json(folder / "transcript.json")
    notes = read_json(folder / "notes.json")
    if not transcript or not notes or notes["transcript_revision"] != transcript["revision"]:
        raise MeetingError("Current transcript and notes are required.")
    output = folder / "uploads"
    output.mkdir(exist_ok=True)
    assets = []
    # Use revision-specific subfolders to avoid modifying files belonging to prior receipts.
    text_dir = output / transcript["revision"][:16]
    text_dir.mkdir(exist_ok=True)
    parts = pack_chunks(transcript_lines(transcript), UPLOAD_LIMIT - 10000)
    for i, content in enumerate(parts, 1):
        name = "full-transcript.md" if len(parts) == 1 else f"full-transcript-part-{i:03d}.md"
        path = text_dir / name
        path.write_text("# Full transcript\n\n" + content + "\n", encoding="utf-8")
        assets.append({"name": name, "kind": "transcript", "path": str(path), "byte_size": path.stat().st_size, "sha256": digest(path),
                       "segment_ids": [int(i) for i in re.findall(r"\*\*\[[^\n]+\] Segment (\d+)\*\*", content)]})
    source = read_json(folder / "source.json")
    text_only = source and Path(source["file"]).suffix in {".txt", ".md"}
    if not text_only:
        audio = normalize(store, folder)
        audio_dir = output / digest(audio)[:16]
        audio_dir.mkdir(exist_ok=True)
        # 10-minute, 64-kbit mono parts are ~4.8 MiB, comfortably below host's 10 MiB cap.
        existing = sorted(audio_dir.glob("recording-part-*.mp3"))
        ready = audio_dir / "READY"
        if not ready.exists():
            run([shutil.which("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(audio),
                 "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "64k", "-f", "segment",
                 "-segment_time", "600", "-reset_timestamps", "1", "-segment_start_number", "1", str(audio_dir / "recording-part-%03d.mp3")])
            ready.touch()
            existing = sorted(audio_dir.glob("recording-part-*.mp3"))
        if not existing:
            raise MeetingError("No recording upload parts were produced.")
        for i, path in enumerate(existing, 1):
            assets.append({"name": path.name, "kind": "recording", "path": str(path), "byte_size": path.stat().st_size,
                           "sha256": digest(path), "start_seconds": (i - 1) * 600})
    if any(a["byte_size"] > UPLOAD_LIMIT for a in assets):
        raise MeetingError("An upload part exceeds Spaces' 10 MiB limit; no publication was marked complete.")
    write_json(folder / "publication-manifest.json", {"transcript_revision": transcript["revision"], "notes_revision": notes["revision"],
                                                     "assets": assets, "prepared_at": now()})


def work(store, meeting_id, phase, model="base", language=None):
    folder = store.folder(meeting_id)
    job_path = folder / f"{phase}-job.json"
    job = {"state": "running", "phase": phase, "pid": os.getpid(), "started_at": now(), "started_epoch": time.time()}
    write_json(job_path, job)
    try:
        details = {}
        if phase == "transcribe":
            details = transcribe(store, folder, model, language)
        elif phase == "prepare":
            prepare(store, folder)
        else:
            raise MeetingError("Unknown worker phase.")
        write_json(job_path, {**job, **details, "state": "ready", "finished_at": now()})
    except Exception as error:
        write_json(job_path, {**job, "state": "error", "error": str(error), "finished_at": now()})
        raise
