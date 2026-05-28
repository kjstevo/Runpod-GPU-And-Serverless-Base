import asyncio
import base64
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

import boto3
import runpod

DATA_DIR = Path(os.environ.get("DATA_DIR", "/workspace"))
JOBS_DIR = DATA_DIR / ".jobs"

S3_BUCKET = os.environ.get("S3_BUCKET_NAME", "vrp9g4opbn")
S3_PRESIGN_EXPIRY = int(os.environ.get("S3_PRESIGN_EXPIRY", "3600"))
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "https://s3api-us-il-1.runpod.io")


def _make_s3_client():
    kwargs = {"endpoint_url": S3_ENDPOINT_URL} if S3_ENDPOINT_URL else {}
    if key_id := os.environ.get("AWS_ACCESS_KEY_ID"):
        kwargs["aws_access_key_id"] = key_id
    if secret := os.environ.get("AWS_SECRET_ACCESS_KEY"):
        kwargs["aws_secret_access_key"] = secret
    if region := os.environ.get("AWS_DEFAULT_REGION"):
        kwargs["region_name"] = region
    return boto3.client("s3", **kwargs)


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _load_job(job_id: str) -> dict | None:
    p = _job_path(job_id)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def _save_job(state: dict) -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _job_path(state["job_id"]).with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.rename(_job_path(state["job_id"]))


_SYSTEM_DIRS = {
    "models", "finished", "output", "yt-dlp", "karaoke_gen_changes",
    "WhisperHallu", "WhisperTimeSync", "venv",
}


def _workspace_subdirs() -> set[Path]:
    return {p for p in DATA_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")}


def _find_output_dir(new_dirs: set[Path], artist: str, title: str) -> Path | None:
    # 1. Exact artist - title match (most reliable)
    exact = DATA_DIR / f"{artist} - {title}"
    if exact in new_dirs:
        return exact

    # 2. Any new dir that already contains an MP4
    with_mp4 = [d for d in new_dirs if any(d.glob("*.mp4"))]
    if with_mp4:
        return with_mp4[0]

    # 3. Any new dir that isn't a known system directory
    candidates = [d for d in new_dirs if d.name not in _SYSTEM_DIRS]
    if candidates:
        return candidates[0]

    return None


def _find_lossless_mp4(directory: Path) -> Path | None:
    mp4s = list(directory.glob("*.mp4"))
    if not mp4s:
        return None
    lossless = [f for f in mp4s if "lossless" in f.name.lower()]
    if lossless:
        return max(lossless, key=lambda f: f.stat().st_size)
    return max(mp4s, key=lambda f: f.stat().st_size)


def _resolve_input(data: dict, job_id: str) -> tuple[str, Path | None]:
    """Returns (source, temp_file_to_cleanup). Priority: file_path > file_data > url."""
    if file_path := data.get("file_path"):
        p = Path(file_path)
        if not p.exists():
            raise ValueError(f"file_path {file_path!r} does not exist")
        size = p.stat().st_size
        if size > MAX_FILE_SIZE:
            raise ValueError(f"file_path exceeds 10 MB limit ({size} bytes)")
        return str(p), None

    if file_data := data.get("file_data"):
        try:
            raw = base64.b64decode(file_data)
        except Exception as e:
            raise ValueError(f"file_data is not valid base64: {e}")
        if len(raw) > MAX_FILE_SIZE:
            raise ValueError(f"Decoded file exceeds 10 MB limit ({len(raw)} bytes)")
        ext = Path(data.get("filename", "input.mp3")).suffix or ".mp3"
        tmp_dir = DATA_DIR / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_file = tmp_dir / f"{job_id}{ext}"
        tmp_file.write_bytes(raw)
        return str(tmp_file), tmp_file

    if url := data.get("url"):
        return url, None

    raise ValueError("One of 'url', 'file_data', or 'file_path' is required")


def _resolve_lyrics(data: dict, job_id: str) -> tuple[str | None, Path | None]:
    """Returns (lyrics_path, temp_file_to_cleanup) or (None, None) if no lyrics provided."""
    if lyrics_path := data.get("lyrics_file_path"):
        p = Path(lyrics_path)
        if not p.exists():
            raise ValueError(f"lyrics_file_path {lyrics_path!r} does not exist")
        return str(p), None

    if lyrics_data := data.get("lyrics_file_data"):
        try:
            raw = base64.b64decode(lyrics_data)
        except Exception as e:
            raise ValueError(f"lyrics_file_data is not valid base64: {e}")
        tmp_dir = DATA_DIR / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_file = tmp_dir / f"{job_id}_lyrics.txt"
        tmp_file.write_bytes(raw)
        return str(tmp_file), tmp_file

    return None, None


async def create_job(data: dict) -> dict:
    artist = data["artist"]
    title = data["title"]
    job_id = str(uuid.uuid4())

    try:
        source, temp_file = _resolve_input(data, job_id)
        lyrics_path, lyrics_temp_file = _resolve_lyrics(data, job_id)
    except ValueError as e:
        return {"error": str(e)}

    if data.get("file_path"):
        source_type = "file_path"
    elif data.get("file_data"):
        source_type = "file_data"
    else:
        source_type = "url"

    state = {
        "job_id": job_id,
        "source_type": source_type,
        "source": data.get("filename", "input.mp3") if source_type == "file_data" else source,
        "artist": artist,
        "title": title,
        "output_dir": None,
        "status": "running",
        "output": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
    }
    _save_job(state)

    before_dirs = _workspace_subdirs()
    cmd = ["karaoke-gen", "-y", "--style_params_json", "/app/style.json", "--subtitle_offset_ms", "-300", "--skip_transcription_review"]
    if lyrics_path:
        cmd += ["--lyrics_file", lyrics_path]
    cmd += [source, artist, title]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(DATA_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        output_lines = []
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="replace")
            output_lines.append(text)
            if len(output_lines) % 10 == 0:
                state["output"] = "".join(output_lines)
                _save_job(state)

        await proc.wait()
        state["output"] = "".join(output_lines)
        state["status"] = "ended_success" if proc.returncode == 0 else "ended_failure"
        state["ended_at"] = datetime.now(timezone.utc).isoformat()

        new_dirs = _workspace_subdirs() - before_dirs
        output_dir = _find_output_dir(new_dirs, artist, title)
        if output_dir:
            state["output_dir"] = str(output_dir)

    except Exception as e:
        state["output"] += f"\nError launching karaoke-gen: {e}"
        state["status"] = "ended_failure"
        state["ended_at"] = datetime.now(timezone.utc).isoformat()
    finally:
        if temp_file and temp_file.exists():
            temp_file.unlink()
        if lyrics_temp_file and lyrics_temp_file.exists():
            lyrics_temp_file.unlink()

    _save_job(state)
    return {"job_id": job_id}


async def get_status(data: dict) -> dict:
    job_id = data.get("job_id")
    state = _load_job(job_id)
    if state is None:
        return {"error": f"Job {job_id!r} not found"}
    return {
        "job_id": job_id,
        "status": state["status"],
        "output": state["output"],
    }


async def download_job(data: dict) -> dict:
    job_id = data.get("job_id")
    state = _load_job(job_id)
    if state is None:
        return {"error": f"Job {job_id!r} not found"}

    if not state.get("output_dir"):
        return {"error": "Output directory not recorded for this job"}
    output_dir = Path(state["output_dir"])
    mp4 = _find_lossless_mp4(output_dir)
    if mp4 is None:
        return {"error": f"No MP4 found in {output_dir}"}

    s3_key = str(mp4.relative_to(DATA_DIR))
    return {"job_id": job_id, "s3_key": s3_key, "filename": mp4.name}


async def finish_job(data: dict) -> dict:
    job_id = data.get("job_id")
    state = _load_job(job_id)
    if state is None:
        return {"error": f"Job {job_id!r} not found"}

    if not state.get("output_dir"):
        return {"error": "Output directory not recorded for this job"}
    output_dir = Path(state["output_dir"])

    mp4 = _find_lossless_mp4(output_dir)
    if mp4 is None:
        return {"error": f"No MP4 found in {output_dir}"}

    finished_dir = DATA_DIR / "finished"
    finished_dir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.copy2, mp4, finished_dir / mp4.name)

    if output_dir.exists():
        await asyncio.to_thread(shutil.rmtree, output_dir)

    return {"job_id": job_id, "status": "cleaned_up", "saved_to": str(finished_dir / mp4.name)}


async def handler(event):
    data = event.get("input", {})
    action = data.get("action")

    if action == "create":
        return await create_job(data)
    elif action == "status":
        return await get_status(data)
    elif action == "download":
        return await download_job(data)
    elif action == "finish":
        return await finish_job(data)
    else:
        return {
            "error": f"Unknown action: {action!r}",
            "valid_actions": ["create", "status", "download", "finish"],
        }


mode_to_run = os.getenv("MODE_TO_RUN", "pod")

print("------- ENVIRONMENT VARIABLES -------")
print("Mode running:", mode_to_run)
print("------- -------------------- -------")

if mode_to_run == "pod":
    async def main():
        usage = (
            "Usage:\n"
            "  python handler.py create <url|/local/path> <artist> <title> [/path/to/lyrics.txt]\n"
            "  python handler.py status <job_id>\n"
            "  python handler.py download <job_id>\n"
            "  python handler.py finish <job_id>"
        )
        args = sys.argv[1:]
        if not args:
            print(usage)
            sys.exit(1)

        action = args[0]
        if action == "create" and len(args) in (4, 5):
            source = args[1]
            if source.startswith("http://") or source.startswith("https://"):
                create_input = {"action": "create", "url": source, "artist": args[2], "title": args[3]}
            else:
                create_input = {"action": "create", "file_path": source, "artist": args[2], "title": args[3]}
            if len(args) == 5:
                create_input["lyrics_file_path"] = args[4]
            event = {"input": create_input}
        elif action in ("status", "download", "finish") and len(args) == 2:
            event = {"input": {"action": action, "job_id": args[1]}}
        else:
            print(usage)
            sys.exit(1)

        response = await handler(event)
        print(response)

    asyncio.run(main())
else:
    runpod.serverless.start({
        "handler": handler,
        "concurrency_modifier": lambda current: 1,
    })
