# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A Docker base image for deploying workloads on RunPod — supporting both **GPU Pod** (interactive dev) and **Serverless** (production) modes from a single image. The primary use case is audio/karaoke processing using `karaoke-gen` with local Whisper transcription.

## Build commands

Standard build and push (must be on a machine with Docker and `linux/amd64` support):

```bash
docker build -t justinrunpod/pod-server-base:1.0 . --push --platform linux/amd64
```

Using [depot](https://depot.dev) (faster cross-platform builds):

```bash
depot build -t justinrunpod/pod-server-base:1.0 . --push --platform linux/amd64
```

Override the base image:

```bash
docker build --build-arg BASE_IMAGE=runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04 \
  -t justinrunpod/pod-server-base:1.0-pytorch2.8.0-cuda12.8 . --push --platform linux/amd64
```

See `README_BUILD.md` for the full version matrix (PyTorch 2.0.1–2.8.0 / CUDA 11.8–12.8).

## Runtime modes

Controlled by `MODE_TO_RUN` env var (set at container launch, not build time):

| Mode | Behavior |
|------|----------|
| `pod` | Starts Jupyter Lab on port 8888; skips `handler.py` — for interactive dev |
| `serverless` | Runs `handler.py` via RunPod serverless runtime |

The `start.sh` also starts nginx and optionally sets up SSH (`PUBLIC_KEY` env var).

## Key files

- **`bootstrap.sh`** — The container CMD entry point (never updated at runtime). At every startup it fetches the latest `handler.py`, `start.sh`, and `pull_changes.py` from `raw.githubusercontent.com`, falls back to baked-in copies on network failure, runs `pull_changes.py`, copies karaoke changes to the venv site-packages, then `exec`s into `start.sh`.
- **`handler.py`** — Async RunPod handler with four actions routed via `event["input"]["action"]`. Push changes here to deploy without rebuilding the image. See **Handler API** below.
- **`start.sh`** — Container startup script; branches on `MODE_TO_RUN`. Push changes here to deploy without rebuilding.
- **`pull_changes.py`** — Run at container startup (via `bootstrap.sh`). Fetches files from the `kjstevo/karaoke-gen` fork that differ from upstream (`nomadkaraoke/karaoke-gen`) and saves them to `karaoke_gen_changes/`. Set `GITHUB_TOKEN` to avoid GitHub API rate limits (60 req/hr unauthenticated vs 5000 authenticated).
- **`requirements.txt`** — Currently empty; add Python dependencies here (requires image rebuild).

## Python environment inside the container

A venv is created at `/app/venv` and activated via `PATH`. Key pre-installed packages: `karaoke-gen[local-whisper]`, `runpod`, `torch==2.8.0+cu128`, `torchaudio==2.8.0+cu128`, `onnxruntime-gpu`, `yt-dlp`, `deno`, spaCy with `en_core_web_sm`.

## Handler API

All requests use the RunPod serverless `/run` or `/runsync` endpoints with `{"input": {"action": "<action>", ...}}`.

| Action | Required fields | Behaviour |
|--------|----------------|-----------|
| `create` | `url`, `artist`, `title` | Launches `karaoke-gen -y --skip_transcription_review <url> <artist> <title>` in `/workspace`. Blocks until the process exits, streaming output to `/workspace/.jobs/{job_id}.json`. Returns `{job_id}`. **Queue serialises these.** |
| `status` | `job_id` | Returns `{job_id, status, output}`. `status` is `running`, `ended_success`, or `ended_failure`. Immediate. |
| `download` | `job_id` | Finds the lossless MP4 in `/workspace/Artist - Title/`, derives its S3 key from its path relative to `/workspace` (prepended with `S3_KEY_PREFIX`), and returns `{job_id, url, filename}` with a presigned URL. No upload — the network volume is already S3-backed. Immediate. |
| `finish` | `job_id` | Deletes `/workspace/Artist - Title/` and all contents. Returns `{job_id, status: "cleaned_up"}`. Immediate. |

Job state is persisted to `/workspace/.jobs/{job_id}.json` on the network volume, so any worker can serve `status`/`download`/`finish` for jobs started on a different worker.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `MODE_TO_RUN` | `pod` | `pod` or `serverless` |
| `WORKSPACE_DIR` | `/app` | Working directory inside container |
| `REPO_OWNER` | `kjstevo` | GitHub owner for bootstrap file pulls |
| `REPO_NAME` | `Runpod-GPU-And-Serverless-Base` | GitHub repo for bootstrap file pulls |
| `REPO_BRANCH` | `main` | Branch to pull `handler.py` / `start.sh` from |
| `WHISPER_MODEL_SIZE` | `large-v2` | Whisper model to use |
| `WHISPER_CACHE_DIR` | `/workspace/models` | Where Whisper models are cached |
| `WHISPER_LANGUAGE` | `en` | Transcription language |
| `ENABLE_LOCAL_WHISPER` | `True` | Use local Whisper (vs. API) |
| `SKIP_CORRECTION` | `False` | Skip lyrics correction step |
| `GITHUB_TOKEN` | *(none)* | GitHub PAT for `pull_changes.py` rate limits |
| `CONCURRENCY_MODIFIER` | `1` | Serverless concurrency tuning |
| `S3_BUCKET_NAME` | *(required for download)* | S3 bucket for MP4 uploads |
| `S3_KEY_PREFIX` | `karaoke` | Key prefix inside the bucket |
| `S3_PRESIGN_EXPIRY` | `3600` | Presigned URL lifetime in seconds |
| `S3_ENDPOINT_URL` | *(none)* | Override for S3-compatible endpoints (e.g. RunPod's S3) |
| `AWS_ACCESS_KEY_ID` | *(standard boto3)* | AWS / S3-compatible credentials |
| `AWS_SECRET_ACCESS_KEY` | *(standard boto3)* | AWS / S3-compatible credentials |
| `AWS_DEFAULT_REGION` | *(standard boto3)* | AWS region |

## Iteration workflow

**No rebuild needed** — push `handler.py` or `start.sh` to GitHub and restart the container. `bootstrap.sh` fetches the latest on every startup.

**Rebuild required** when changing:
- System packages (`apt-get` installs)
- Python dependencies (`requirements.txt`, `pip install` lines in Dockerfile)
- `bootstrap.sh` itself
- `pull_changes.py` (though it's also fetched at runtime, so a restart alone works too)

**General flow:**
1. Deploy to a GPU/CPU Pod using the provided RunPod template
2. Experiment interactively — install packages, test `handler.py` directly with `python handler.py`
3. Push `handler.py` / `start.sh` changes to GitHub; restart the container to pick them up
4. If dependencies changed, rebuild and push the image, then redeploy
5. Switch to serverless by redeploying with `MODE_TO_RUN=serverless`

## RunPod CLI

The `.agents/skills/runpodctl/SKILL.md` skill documents the `runpodctl` CLI. Use the `runpodctl` skill when managing pods, serverless endpoints, templates, volumes, or models from the CLI.
