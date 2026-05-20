import asyncio
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
import runpod

DATA_DIR = Path("/workspace")
JOBS_DIR = DATA_DIR / ".jobs"

S3_BUCKET = os.environ.get("S3_BUCKET_NAME", "vrp9g4opbn")
S3_PRESIGN_EXPIRY = int(os.environ.get("S3_PRESIGN_EXPIRY", "3600"))
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "https://s3api-us-il-1.runpod.io")


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


def _workspace_subdirs() -> set[Path]:
    return {p for p in DATA_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")}


def _find_lossless_mp4(directory: Path) -> Path | None:
    mp4s = list(directory.glob("*.mp4"))
    if not mp4s:
        return None
    lossless = [f for f in mp4s if "lossless" in f.name.lower()]
    if lossless:
        return max(lossless, key=lambda f: f.stat().st_size)
    return max(mp4s, key=lambda f: f.stat().st_size)


async def create_job(data: dict) -> dict:
    url = data["url"]
    artist = data["artist"]
    title = data["title"]

    job_id = str(uuid.uuid4())
    state = {
        "job_id": job_id,
        "url": url,
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
    cmd = ["karaoke-gen", "-y", "--skip_transcription_review", url, artist, title]

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
        if new_dirs:
            state["output_dir"] = str(next(iter(new_dirs)))

    except Exception as e:
        state["output"] += f"\nError launching karaoke-gen: {e}"
        state["status"] = "ended_failure"
        state["ended_at"] = datetime.now(timezone.utc).isoformat()

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

    if not S3_BUCKET:
        return {"error": "S3_BUCKET_NAME env var not set"}

    s3_kwargs = {}
    if S3_ENDPOINT_URL:
        s3_kwargs["endpoint_url"] = S3_ENDPOINT_URL

    s3 = boto3.client("s3", **s3_kwargs)
    s3_key = str(mp4.relative_to(DATA_DIR))

    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": s3_key},
        ExpiresIn=S3_PRESIGN_EXPIRY,
    )

    return {"job_id": job_id, "url": url, "filename": mp4.name}


async def finish_job(data: dict) -> dict:
    job_id = data.get("job_id")
    state = _load_job(job_id)
    if state is None:
        return {"error": f"Job {job_id!r} not found"}

    if not state.get("output_dir"):
        return {"error": "Output directory not recorded for this job"}
    output_dir = Path(state["output_dir"])
    if output_dir.exists():
        await asyncio.to_thread(shutil.rmtree, output_dir)

    return {"job_id": job_id, "status": "cleaned_up"}


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
            "  python handler.py create <url> <artist> <title>\n"
            "  python handler.py status <job_id>\n"
            "  python handler.py download <job_id>\n"
            "  python handler.py finish <job_id>"
        )
        args = sys.argv[1:]
        if not args:
            print(usage)
            sys.exit(1)

        action = args[0]
        if action == "create" and len(args) == 4:
            event = {"input": {"action": "create", "url": args[1], "artist": args[2], "title": args[3]}}
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
