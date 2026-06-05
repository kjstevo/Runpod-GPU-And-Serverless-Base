# Streaming Handler Design

**Date:** 2026-06-04

## Goal

Convert the RunPod serverless handler from a blocking create action to an async generator
that streams karaoke-gen output via RunPod's `/stream` endpoint. Simplify submit_karaoke.py
to poll a single endpoint instead of dual-polling (S3 + RunPod status).

## handler.py changes

### Handler becomes an async generator

```python
async def handler(job):
    data = job.get("input", {})
    action = data.get("action")

    if action == "create":
        async for chunk in create_job_stream(job, data):
            yield chunk
    elif action == "status":
        yield await get_status(data)
    elif action == "download":
        yield await download_job(data)
    elif action == "finish":
        yield await finish_job(data)
    else:
        yield {"error": f"Unknown action: {action!r}"}
```

Enable aggregate stream so `/runsync` still works for single-yield actions:

```python
runpod.serverless.start({
    "handler": handler,
    "return_aggregate_stream": True,
    "concurrency_modifier": lambda current: 1,
})
```

### create_job becomes create_job_stream (async generator)

- Writes initial job state to S3 (unchanged)
- Starts karaoke-gen subprocess (unchanged)
- Yields each output line as it arrives: `{"type": "output", "line": text}`
- On completion, writes final job state to S3 and yields:
  `{"type": "done", "job_id": job_id, "status": "ended_success|ended_failure"}`
- S3 job state file is kept as a resilience layer for cross-worker status queries

## submit_karaoke.py changes

### poll_until_done replaced by poll_stream

Polls `GET /stream/{runpod_job_id}` every 5s. RunPod returns only new chunks each call.

```
{"stream": [{"output": chunk}, ...], "status": "IN_PROGRESS|COMPLETED|FAILED"}
```

- `type == "output"` chunks: print line, scan for localhost URL to open review browser
- `type == "done"` chunk: return final job status
- `status == "COMPLETED"` with no done chunk: return empty (shouldn't happen normally)
- `status in (FAILED, CANCELLED, TIMED_OUT)`: exit with error

### What is removed

- `last_runpod_check` / 30s interval timer
- S3 read in the hot polling loop
- `read_job_from_s3` call in the loop (kept for post-completion status confirmation)

### What stays

- `/run` submission (unchanged)
- `call_action` via `/runsync` for `download` and `finish` (single-yield, unchanged)
- S3 download of the finished MP4 (unchanged)
- Browser auto-open on localhost URL detection (unchanged)
- `read_job_from_s3` used once after stream completes to confirm `ended_success`
