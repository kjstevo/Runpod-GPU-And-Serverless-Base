# Streaming Handler Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert the RunPod handler to an async generator that streams karaoke-gen output via RunPod's `/stream` endpoint, and simplify submit_karaoke.py to poll a single endpoint.

**Architecture:** `handler()` becomes an async generator — `create_job` becomes `create_job_stream` that yields `{"type":"output","line":...}` chunks then a final `{"type":"done",...}` chunk. `status`/`download`/`finish` each yield once (single-item generators). submit_karaoke.py replaces its dual-polling loop with a single `poll_stream()` that reads `/stream/{runpod_job_id}`.

**Tech Stack:** Python asyncio, RunPod serverless SDK (`runpod`), `requests`, boto3

**Worktree:** `.worktrees/feature/streaming-handler`

---

### Task 1: Convert `create_job` to `create_job_stream` async generator

**Files:**
- Modify: `.worktrees/feature/streaming-handler/handler.py`

**Step 1: Rename and change signature**

In `handler.py`, rename `async def create_job(data: dict) -> dict:` to `async def create_job_stream(job: dict, data: dict)` and remove the return type annotation. The `job` parameter is the raw RunPod job dict (needed for future progress_update use, currently passed for consistency).

**Step 2: Replace the final `return` with `yield`**

The current function ends with:
```python
    _save_job(state)
    return {"job_id": job_id}
```

Replace with:
```python
    _save_job(state)
    yield {"type": "done", "job_id": job_id, "status": state["status"]}
```

**Step 3: Yield output lines as they arrive**

Find the inner loop that accumulates output:
```python
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="replace")
            output_lines.append(text)
            now = asyncio.get_event_loop().time()
            if len(output_lines) % 10 == 0 or (now - last_save) > 5:
                state["output"] = "".join(output_lines)
                _save_job(state)
                last_save = now
```

Replace with:
```python
        async for line in proc.stdout:
            text = line.decode("utf-8", errors="replace")
            output_lines.append(text)
            yield {"type": "output", "line": text}
            now = asyncio.get_event_loop().time()
            if len(output_lines) % 10 == 0 or (now - last_save) > 5:
                state["output"] = "".join(output_lines)
                _save_job(state)
                last_save = now
```

**Step 4: Handle the exception path**

The existing `except` block sets `state["status"] = "ended_failure"`. After that block and before the finally, there is currently no yield in the error path. Add a yield in the except block so the generator always terminates with a done chunk regardless of success or failure. The except block becomes:

```python
    except Exception as e:
        state["output"] += f"\nError launching karaoke-gen: {e}"
        state["status"] = "ended_failure"
        state["ended_at"] = datetime.now(timezone.utc).isoformat()
        yield {"type": "output", "line": f"\nError launching karaoke-gen: {e}\n"}
```

The final `_save_job` + `yield done` at the end of the function (after the finally) handles the terminal yield for both success and error paths.

**Step 5: Commit**

```bash
git add handler.py
git commit -m "refactor: convert create_job to create_job_stream async generator"
```

---

### Task 2: Convert `handler()` to an async generator

**Files:**
- Modify: `.worktrees/feature/streaming-handler/handler.py`

**Step 1: Change handler signature and body**

Replace:
```python
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
```

With:
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
        yield {
            "error": f"Unknown action: {action!r}",
            "valid_actions": ["create", "status", "download", "finish"],
        }
```

**Step 2: Update `runpod.serverless.start` to enable aggregate stream**

Find:
```python
    runpod.serverless.start({
        "handler": handler,
        "concurrency_modifier": lambda current: 1,
    })
```

Replace with:
```python
    runpod.serverless.start({
        "handler": handler,
        "return_aggregate_stream": True,
        "concurrency_modifier": lambda current: 1,
    })
```

`return_aggregate_stream: True` means `/runsync` and `/run` still work for callers that don't use `/stream` — they receive all yielded values concatenated into a list in `output`.

**Step 3: Update pod-mode CLI to consume the generator**

The pod-mode `main()` currently does:
```python
        response = await handler(event)
        print(response)
```

Replace with:
```python
        async for chunk in handler(event):
            if chunk.get("type") == "output":
                print(chunk["line"], end="", flush=True)
            else:
                print(chunk)
```

**Step 4: Commit**

```bash
git add handler.py
git commit -m "refactor: convert handler to async generator, enable return_aggregate_stream"
```

---

### Task 3: Replace `poll_until_done` with `poll_stream` in submit_karaoke.py

**Files:**
- Modify: `.worktrees/feature/streaming-handler/submit_karaoke.py`

**Step 1: Add `poll_stream` function**

Replace the entire `poll_until_done` function with:

```python
def poll_stream(runpod_job_id: str) -> dict:
    """Poll /stream/{id} until job completes, printing output and returning the done chunk."""
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
            if chunk.get("type") == "output":
                line = chunk.get("line", "")
                print(
                    f"  {line}".encode(sys.stdout.encoding, errors="replace").decode(sys.stdout.encoding),
                    end="",
                    flush=True,
                )
                if not browser_opened and _LOCALHOST_URL_RE.search(line):
                    pod_id = ""
                    review_url = f"https://RUNPOD_POD_ID-8000.proxy.runpod.net/en/app/jobs/local/review"
                    # pod_id comes from S3 job state — read once to get it
                    s3_state = read_job_from_s3_safe()
                    if s3_state:
                        pod_id = s3_state.get("pod_id", "")
                    if pod_id:
                        review_url = f"https://{pod_id}-8000.proxy.runpod.net/en/app/jobs/local/review"
                        log(f"Opening review: {review_url}")
                        webbrowser.open(review_url)
                        browser_opened = True
                    else:
                        log("WARNING: karaoke-gen wants review but pod_id unknown")
            elif chunk.get("type") == "done":
                return chunk

        status = data.get("status", "UNKNOWN")
        if status == "COMPLETED":
            return {}
        if status in ("FAILED", "CANCELLED", "TIMED_OUT"):
            log(f"RunPod job ended: {status}")
            log(json.dumps(data, indent=2))
            sys.exit(1)

        time.sleep(5)
```

Note: The browser-opening block needs the `pod_id` from the S3 job state. We read it lazily from S3 (one call, not in a loop). Add a helper just above `poll_stream`:

```python
def read_job_from_s3_safe(job_id: str) -> dict | None:
    try:
        obj = _s3.get_object(Bucket=S3_BUCKET, Key=f".jobs/{job_id}.json")
        return json.loads(obj["Body"].read())
    except Exception:
        return None
```

Wait — `read_job_from_s3` already exists but takes `job_id` as a parameter. The issue is that `poll_stream` doesn't have `our_job_id` in scope. Fix: pass `our_job_id` as a second parameter to `poll_stream`:

```python
def poll_stream(runpod_job_id: str, our_job_id: str) -> dict:
```

And inside the browser-open block:
```python
                    s3_state = read_job_from_s3(our_job_id)
```

**Step 2: Update the call site in `main()`**

Find:
```python
    result = poll_until_done(runpod_job_id, our_job_id, interval=30)

    output = result.get("output", {})
    if "error" in output:
        log(f"Handler error: {output['error']}")
        sys.exit(1)

    # Confirm final job status
    status_result = read_job_from_s3(our_job_id) or {}
    job_status = status_result.get("status", "unknown")
    log(f"Job status: {job_status}")
    if job_status != "ended_success":
        log("ERROR: karaoke-gen did not succeed.")
        sys.exit(1)
```

Replace with:
```python
    done_chunk = poll_stream(runpod_job_id, our_job_id)

    # done_chunk is {"type":"done","job_id":...,"status":"ended_success|ended_failure"}
    # or {} if RunPod reported COMPLETED without a done chunk (shouldn't happen normally)
    job_status = done_chunk.get("status") or (read_job_from_s3(our_job_id) or {}).get("status", "unknown")
    log(f"Job status: {job_status}")
    if job_status != "ended_success":
        log("ERROR: karaoke-gen did not succeed.")
        sys.exit(1)
```

**Step 3: Remove the now-unused `last_runpod_check` / interval imports**

The `poll_until_done` removal also removes the need for `interval` logic. Verify nothing else references `poll_until_done` and delete it entirely.

**Step 4: Commit**

```bash
git add submit_karaoke.py
git commit -m "feat: replace poll_until_done with poll_stream using RunPod /stream endpoint"
```

---

### Task 4: Smoke-test in pod mode

**No automated tests exist for this project. Verify manually.**

**Step 1: Confirm handler.py runs without import errors**

In the worktree directory (or on a machine with `runpod` installed):
```bash
python -c "import handler; print('OK')"
```
Expected: `OK` (no import errors).

**Step 2: Confirm pod-mode CLI works for status/download/finish**

```bash
python handler.py status some-fake-job-id
```
Expected: `{'error': "Job 'some-fake-job-id' not found"}` (no crash, generator consumed correctly).

**Step 3: Commit any fixups found**

```bash
git add handler.py submit_karaoke.py
git commit -m "fix: smoke test fixups"
```

---

### Task 5: Push branch and verify end-to-end on RunPod

**Step 1: Push branch**

```bash
git push -u origin feature/streaming-handler
```

**Step 2: Deploy handler.py to a running pod**

Restart the RunPod container so `bootstrap.sh` fetches the new `handler.py` from this branch.
Set `REPO_BRANCH=feature/streaming-handler` on the pod, or merge to `main` first.

**Step 3: Run submit_karaoke.py against a real song**

```bash
python submit_karaoke.py "https://www.youtube.com/watch?v=..." --artist "Artist" --title "Title"
```

Expected:
- Output lines stream in via `/stream` endpoint
- Browser opens for review when karaoke-gen prompts
- Job completes with `ended_success`
- MP4 downloads to `~/Downloads/`

**Step 4: Merge to main when verified**

Use `superpowers:finishing-a-development-branch` skill to merge, clean up worktree, and delete the branch.
