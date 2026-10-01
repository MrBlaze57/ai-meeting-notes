from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess
import time

from core import MeetingError, UPLOAD_LIMIT, digest, now, pack_chunks, read_json, transcript_lines, write_json


def run(command):
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
    audio = normalize(store, folder)
    if audio is None:
        if not (folder / "transcript.json").is_file():
            raise MeetingError("Imported transcript is missing.")
        return
    output = folder / "whisper"
    output.mkdir(exist_ok=True)
    cmd = [shutil.which("whisper"), str(audio), "--model", model, "--output_dir", str(output),
           "--output_format", "json", "--fp16", "False", "--threads", str(min(8, os.cpu_count() or 4)), "--verbose", "False"]
    if language:
        cmd += ["--language", language]
    run(cmd)
    raw = read_json(output / "audio.json")
    if not raw:
        raise MeetingError("Whisper finished without a transcript file.")
    store.put_transcript(folder, raw.get("segments", []), "Whisper " + model)


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
        if phase == "transcribe":
            transcribe(store, folder, model, language)
        elif phase == "prepare":
            prepare(store, folder)
        else:
            raise MeetingError("Unknown worker phase.")
        write_json(job_path, {**job, "state": "ready", "finished_at": now()})
    except Exception as error:
        write_json(job_path, {**job, "state": "error", "error": str(error), "finished_at": now()})
        raise
