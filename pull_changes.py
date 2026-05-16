#!/usr/bin/env python3
"""
Download files from karaoke_gen/ in kjstevo/karaoke-gen that differ from
the upstream (nomadkaraoke/karaoke-gen) and save them to karaoke_gen_changes/.

No local git clone required. Uses the GitHub API only.

Optional: set GITHUB_TOKEN env var to avoid rate limits (60 req/hr unauthenticated,
5000 req/hr authenticated).
"""

import base64
import fnmatch
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

FORK_OWNER = "kjstevo"
FORK_REPO = "karaoke-gen"
FORK_BRANCH = "main"
SUBDIR = "karaoke_gen"
OUTPUT_DIR = "karaoke_gen_changes"

IGNORE_PATTERNS = [
    "CLAUDE.md",
    "*.md",
    "*.rst",
    "*.txt",
    "*.pyc",
    "*.pyo",
    "__pycache__/*",
    ".gitignore",
    ".gitattributes",
    "*.json",
    "*.yaml",
    "*.yml",
    "*.toml",
    "*.cfg",
    "*.ini",
]


def gh_request(url: str) -> dict | list:
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"ERROR {e.code} fetching {url}: {body}", file=sys.stderr)
        sys.exit(1)


def get_upstream(fork_owner: str, fork_repo: str) -> tuple[str, str, str]:
    """Return (upstream_owner, upstream_repo, upstream_default_branch)."""
    data = gh_request(f"https://api.github.com/repos/{fork_owner}/{fork_repo}")
    if not data.get("fork"):
        print(f"ERROR: {fork_owner}/{fork_repo} is not a fork.", file=sys.stderr)
        sys.exit(1)
    parent = data["parent"]
    return parent["owner"]["login"], parent["name"], parent["default_branch"]


def get_changed_files(
    upstream_owner: str,
    upstream_repo: str,
    upstream_branch: str,
    fork_owner: str,
    fork_branch: str,
) -> list[dict]:
    """
    Return list of file dicts (filename, status) that differ between
    upstream and the fork, scoped to SUBDIR. Handles pagination.
    """
    # Cross-repo compare: upstream base ... fork head
    base = f"{upstream_branch}"
    head = f"{fork_owner}:{fork_branch}"
    url = (
        f"https://api.github.com/repos/{upstream_owner}/{upstream_repo}"
        f"/compare/{base}...{head}?per_page=100"
    )

    changed = []
    page = 1
    while url:
        data = gh_request(url + (f"&page={page}" if page > 1 else ""))
        for f in data.get("files", []):
            if f["filename"].startswith(SUBDIR + "/") and not is_ignored(f["filename"]):
                changed.append(f)
        # GitHub compare doesn't paginate files beyond 300; check anyway
        if len(data.get("files", [])) < 100:
            break
        page += 1

    return changed


def is_ignored(rel_path: str) -> bool:
    name = Path(rel_path).name
    for pattern in IGNORE_PATTERNS:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel_path, pattern):
            return True
    return False


def download_file(fork_owner: str, fork_repo: str, fork_branch: str, path: str) -> bytes:
    """Download a file's raw content from the fork."""
    data = gh_request(
        f"https://api.github.com/repos/{fork_owner}/{fork_repo}"
        f"/contents/{path}?ref={fork_branch}"
    )
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"])
    # Fallback: fetch raw download URL
    raw_url = data.get("download_url")
    if not raw_url:
        print(f"ERROR: no download_url for {path}", file=sys.stderr)
        sys.exit(1)
    with urllib.request.urlopen(raw_url) as resp:
        return resp.read()


def main() -> None:
    output_dir = Path(OUTPUT_DIR)

    print(f"Fetching fork info for {FORK_OWNER}/{FORK_REPO}...")
    upstream_owner, upstream_repo, upstream_branch = get_upstream(FORK_OWNER, FORK_REPO)
    print(f"Upstream: {upstream_owner}/{upstream_repo} (branch: {upstream_branch})")

    print(f"\nComparing {upstream_owner}/{upstream_repo}:{upstream_branch} "
          f"<-> {FORK_OWNER}/{FORK_REPO}:{FORK_BRANCH} (scoped to {SUBDIR}/)...")

    changed = get_changed_files(
        upstream_owner, upstream_repo, upstream_branch,
        FORK_OWNER, FORK_BRANCH,
    )

    if not changed:
        print("No relevant differences found.")
        return

    print(f"\nFound {len(changed)} changed file(s):")
    for f in changed:
        print(f"  [{f['status']:8s}] {f['filename']}")

    # Clear and recreate output directory
    if output_dir.exists():
        import shutil
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    print(f"\nDownloading to {output_dir.resolve()} ...")
    skipped = []
    for f in changed:
        path = f["filename"]
        if f["status"] == "removed":
            print(f"  SKIP (removed in fork): {path}")
            skipped.append(path)
            continue

        content = download_file(FORK_OWNER, FORK_REPO, FORK_BRANCH, path)
        dest = output_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        print(f"  Saved: {path}")

    written = len(changed) - len(skipped)
    print(f"\nDone. {written} file(s) written to '{OUTPUT_DIR}/'.")


if __name__ == "__main__":
    main()
