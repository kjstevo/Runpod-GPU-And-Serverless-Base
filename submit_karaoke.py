"""
End-to-end karaoke job runner:
  1. Download audio from YouTube via yt-dlp
  2. Submit create job to RunPod serverless (file_data, base64)
  3. Poll RunPod /status until COMPLETED or FAILED
  4. Download the finished MP4 from S3
  5. Call finish action
  6. Delete the original yt-dlp download
"""

import argparse
import base64
import configparser
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
import webbrowser
from pathlib import Path

import boto3
import requests
from botocore.config import Config

# ── Config ────────────────────────────────────────────────────────────────────
_CREDS_FILE = Path(__file__).parent / "credentials.txt"
if not _CREDS_FILE.exists():
    print(f"ERROR: credentials file not found: {_CREDS_FILE}", file=sys.stderr)
    sys.exit(1)

_cfg = configparser.ConfigParser()
_cfg.read(_CREDS_FILE)

ENDPOINT_ID  = _cfg["runpod"]["endpoint_id"]
API_KEY      = _cfg["runpod"]["api_key"]
RUNPOD_BASE  = f"https://api.runpod.ai/v2/{ENDPOINT_ID}"
HEADERS      = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

S3_BUCKET    = "vrp9g4opbn"
S3_ENDPOINT  = "https://s3api-us-il-1.runpod.io"
S3_KEY_ID    = _cfg["runpods3"]["aws_access_key_id"]
S3_SECRET    = _cfg["runpods3"]["aws_secret_access_key"]

DOWNLOAD_DIR  = Path.home() / "Downloads"
MAX_FILE_SIZE = 10 * 1024 * 1024

_s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=S3_KEY_ID,
    aws_secret_access_key=S3_SECRET,
    region_name="us-il-1",
    config=Config(signature_version="s3v4"),
)
# ─────────────────────────────────────────────────────────────────────────────


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def read_job_from_s3(job_id: str) -> dict | None:
    try:
        obj = _s3.get_object(Bucket=S3_BUCKET, Key=f".jobs/{job_id}.json")
        return json.loads(obj["Body"].read())
    except Exception:
        return None


AUDIO_EXTS = {".mp3", ".m4a", ".opus", ".ogg", ".flac", ".wav", ".aac", ".webm"}


def download_from_youtube(url: str) -> Path:
    log(f"Downloading audio from: {url}")
    tmp_dir = DOWNLOAD_DIR / f"ytdlp-{uuid.uuid4().hex[:8]}"
    tmp_dir.mkdir()
    dest: Path | None = None
    try:
        result = subprocess.run(
            ["yt-dlp", "-x", "--audio-quality", "0",
             "--no-playlist",
             "--no-progress",
             "-o", str(tmp_dir / "%(title)s.%(ext)s"), url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.stderr:
            print(result.stderr, flush=True)
        if result.returncode != 0:
            log("ERROR: yt-dlp failed")
            sys.exit(1)
        audio_files = [f for f in tmp_dir.iterdir() if f.suffix.lower() in AUDIO_EXTS]
        if not audio_files:
            log("ERROR: yt-dlp succeeded but no audio file found")
            sys.exit(1)
        dest = DOWNLOAD_DIR / audio_files[0].name
        audio_files[0].replace(dest)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    log(f"Downloaded: {dest.name} ({dest.stat().st_size / 1024:.0f} KB)")
    return dest


def submit_job(input_file: Path, artist: str, title: str, our_job_id: str, lyrics_file: Path | None = None) -> str:
    size = input_file.stat().st_size
    if size > MAX_FILE_SIZE:
        log(f"ERROR: file is {size/1024/1024:.1f} MB — handler limit is 10 MB")
        sys.exit(1)

    log(f"Reading {input_file.name} ({size/1024:.0f} KB)")
    encoded = base64.b64encode(input_file.read_bytes()).decode()

    payload = {
        "input": {
            "action":    "create",
            "job_id":    our_job_id,
            "file_data": encoded,
            "filename":  input_file.name,
            "artist":    artist,
            "title":     title,
        }
    }

    if lyrics_file is not None:
        log(f"Attaching lyrics file: {lyrics_file.name}")
        payload["input"]["lyrics_file_data"] = base64.b64encode(lyrics_file.read_bytes()).decode()

    log("Submitting job to RunPod serverless…")
    r = requests.post(f"{RUNPOD_BASE}/run", headers=HEADERS, json=payload, timeout=30)
    if r.status_code == 401:
        log("ERROR: 401 Unauthorized — API key may be read-only or invalid")
        sys.exit(1)
    r.raise_for_status()
    data = r.json()
    runpod_job_id = data["id"]
    log(f"Queued. RunPod job ID: {runpod_job_id}  (status: {data.get('status')})")
    return runpod_job_id


_LOCALHOST_URL_RE = re.compile(r'https?://localhost[^\s]*')


def poll_stream(runpod_job_id: str, our_job_id: str) -> dict:
    """Poll /stream/{id} until job completes. Returns the final done/error chunk."""
    url = f"{RUNPOD_BASE}/stream/{runpod_job_id}"
    browser_opened = False

    while True:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()

        for item in data.get("stream", []):
            chunk = item.get("output", {})
            if not isinstance(chunk, dict):
                continue
            chunk_type = chunk.get("type")
            if chunk_type == "output":
                line = chunk.get("line", "")
                print(
                    f"  {line}".encode(sys.stdout.encoding, errors="replace").decode(sys.stdout.encoding),
                    end="",
                    flush=True,
                )
                if not browser_opened and _LOCALHOST_URL_RE.search(line):
                    s3_state = read_job_from_s3(our_job_id) or {}
                    pod_id = s3_state.get("pod_id", "")
                    if pod_id:
                        review_url = f"https://{pod_id}-8000.proxy.runpod.net/en/app/jobs/local/review"
                        log(f"Opening review: {review_url}")
                        webbrowser.open(review_url)
                        browser_opened = True
                    else:
                        log("WARNING: karaoke-gen wants review but pod_id unknown")
            elif chunk_type in ("done", "error"):
                return chunk

        status = data.get("status", "UNKNOWN")
        if status == "COMPLETED":
            return {}
        if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
            log(f"RunPod job ended: {status}")
            log(json.dumps(data, indent=2))
            sys.exit(1)

        time.sleep(5)


def call_action(action: str, job_id: str) -> dict:
    payload = {"input": {"action": action, "job_id": job_id}}
    r = requests.post(f"{RUNPOD_BASE}/runsync", headers=HEADERS, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    return data.get("output", data)


def main():
    parser = argparse.ArgumentParser(description="Submit a YouTube song to RunPod karaoke generation")
    parser.add_argument("youtube_url", help="YouTube URL to download and process")
    parser.add_argument("--artist", required=True, help="Artist name")
    parser.add_argument("--title", required=True, help="Song title")
    parser.add_argument("--lyrics", metavar="PATH", help="Path to a lyrics .txt file to pass to karaoke-gen")
    args = parser.parse_args()

    lyrics_file: Path | None = None
    if args.lyrics:
        lyrics_file = Path(args.lyrics)
        if not lyrics_file.exists():
            log(f"ERROR: lyrics file not found: {lyrics_file}")
            sys.exit(1)

    # 1. Download from YouTube
    input_file = download_from_youtube(args.youtube_url)

    # 2. Generate job ID now so we can stream status before the create job completes
    our_job_id = str(uuid.uuid4())
    log(f"Internal job ID: {our_job_id}")

    # 3. Submit to RunPod
    runpod_job_id = submit_job(input_file, args.artist, args.title, our_job_id, lyrics_file)

    # 4. Poll until karaoke-gen finishes, streaming output along the way
    done_chunk = poll_stream(runpod_job_id, our_job_id)

    if done_chunk.get("type") == "error":
        log(f"Handler error: {done_chunk.get('message', 'unknown error')}")
        sys.exit(1)

    job_status = done_chunk.get("status") or (read_job_from_s3(our_job_id) or {}).get("status", "unknown")
    log(f"Job status: {job_status}")
    if job_status != "ended_success":
        log("ERROR: karaoke-gen did not succeed.")
        sys.exit(1)

    # 5. Get S3 key and download the finished MP4
    log("Requesting S3 key from handler…")
    dl = call_action("download", our_job_id)
    if "error" in dl:
        log(f"Download action error: {dl['error']}")
        sys.exit(1)

    s3_key   = dl["s3_key"]
    filename = dl["filename"]
    log(f"File: {filename}  (s3_key: {s3_key})")

    dest = DOWNLOAD_DIR / filename
    log(f"Downloading to {dest}")
    for attempt in range(1, 7):
        try:
            _s3.download_file(S3_BUCKET, s3_key, str(dest))
            break
        except Exception as e:
            if attempt == 6:
                log(f"Download failed after 6 attempts: {e}")
                sys.exit(1)
            wait = attempt * 10
            log(f"Download attempt {attempt} failed ({e}), retrying in {wait}s…")
            time.sleep(wait)
    log(f"Download complete: {dest}")

    # 6. Finish (cleanup workspace)
    log("Calling finish action…")
    # finish = call_action("finish", our_job_id)
    log("Finish: skipped")

    # 7. Delete the original yt-dlp download
    log(f"Deleting original download: {input_file.name}")
    input_file.unlink()

    log("Done.")
    log(f"Saved to: {dest}")


if __name__ == "__main__":
    main()
