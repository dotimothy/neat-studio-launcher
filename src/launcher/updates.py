"""Check GitHub for newer studio (or launcher) versions, by commit hash.

How the installed commit is found:

* git checkout: its HEAD and branch. GitHub's compare API then says how many
  commits the checkout is behind (or ahead of) the same branch on GitHub.
* downloaded copy (no .git): every file is hashed the way git hashes blobs and
  compared with the file hashes in the latest commit's tree on GitHub. An
  exact match means up to date. Otherwise the recent commits are tried the same
  way to find which one is installed, and so how many commits behind it is. A
  match is remembered in .neat-launcher-version so later checks start there.

GitHub allows 60 unauthenticated API calls an hour; trees are immutable and
cached for good, commit lists for a few minutes. GITHUB_TOKEN raises the limit.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION_FILE = ".neat-launcher-version"
CACHE_SECONDS = 300
CANDIDATES = 6            # recent commits tried when a downloaded copy is not the latest

_cache: dict[str, tuple[float, object]] = {}      # commit lists (short-lived)
_trees: dict[tuple[str, str], dict] = {}           # tree sha -> {path: (blob sha, mode)} (immutable)
_blobs: dict[tuple[str, int, int], str] = {}       # (path, mtime_ns, size) -> blob sha


class NotOnGitHub(Exception):
    pass


def _api(path: str, timeout: float = 10.0):
    req = urllib.request.Request(
        f"https://api.github.com/{path}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "neat-studio-launcher"})
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _commit_info(c: dict) -> dict:
    return {
        "sha": c["sha"],
        "tree": c["commit"]["tree"]["sha"],
        "date": c["commit"]["committer"]["date"],
        "message": c["commit"]["message"].splitlines()[0][:140],
        "url": c.get("html_url", ""),
    }


def recent_commits(repo: str, branch: str = "main", n: int = CANDIDATES) -> list[dict]:
    """The newest commits on a branch, newest first (cached a few minutes)."""
    key = f"{repo}@{branch}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]                                   # type: ignore[return-value]
    data = _api(f"repos/{repo}/commits?sha={urllib.request.quote(branch, safe='')}&per_page={n}")
    commits = [_commit_info(c) for c in data]
    if not commits:
        raise NotOnGitHub(f"{repo} has no commits on {branch}")
    _cache[key] = (time.time(), commits)
    return commits


def latest_commit(repo: str, branch: str = "main") -> dict:
    return recent_commits(repo, branch)[0]


def remote_tree(repo: str, tree_sha: str) -> dict:
    """{path: (blob sha, mode)} for every file in a commit's tree."""
    key = (repo, tree_sha)
    if key not in _trees:
        data = _api(f"repos/{repo}/git/trees/{tree_sha}?recursive=1", timeout=20)
        if data.get("truncated"):
            raise RuntimeError("the repository is too large to compare file by file")
        _trees[key] = {e["path"]: (e["sha"], e["mode"]) for e in data["tree"] if e["type"] == "blob"}
    return _trees[key]


# -- local hashing -------------------------------------------------------------
def blob_sha(path: Path, mode: str = "100644") -> str | None:
    """The git blob hash of a file (or a symlink's target), None if missing."""
    try:
        if mode == "120000":
            data = os.readlink(path).encode()
            return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _blobs:
        h = hashlib.sha1(b"blob %d\0" % st.st_size)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _blobs[key] = h.hexdigest()
    return _blobs[key]


def _export_ignored(d: Path) -> list[str]:
    """Patterns a GitHub download leaves out (export-ignore in .gitattributes)."""
    out = []
    try:
        for line in (d / ".gitattributes").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and "export-ignore" in parts[1:]:
                out.append(parts[0].lstrip("/"))
    except OSError:
        pass
    return out


def _ignored(path: str, patterns: list[str]) -> bool:
    for p in patterns:
        p = p.rstrip("/")
        if fnmatch.fnmatch(path, p) or path.startswith(p + "/") or fnmatch.fnmatch(path.rsplit("/", 1)[-1], p):
            return True
    return False


def diff_against(d: Path, tree: dict) -> list[str]:
    """Files in the commit that are missing or different in the local copy."""
    skip = _export_ignored(d)
    return sorted(p for p, (sha, mode) in tree.items()
                  if not _ignored(p, skip) and blob_sha(d / p, mode) != sha)


# -- installed state -------------------------------------------------------------
def _git(d: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True, timeout=10)
    return r.stdout.strip() if r.returncode == 0 else ""


def git_state(d: Path) -> dict | None:
    if not (d / ".git").exists():
        return None
    sha = _git(d, "rev-parse", "HEAD")
    if not sha:
        return None
    branch = _git(d, "rev-parse", "--abbrev-ref", "HEAD")
    upstream = _git(d, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream and "/" in upstream:
        branch = upstream.split("/", 1)[1]            # origin/feature/x -> feature/x
    dirty = bool(_git(d, "status", "--porcelain", "--untracked-files=no"))
    return {"sha": sha, "branch": branch if branch != "HEAD" else "", "dirty": dirty}


def recorded(d: Path) -> dict | None:
    try:
        data = json.loads((d / VERSION_FILE).read_text())
        return data if data.get("sha") else None
    except (OSError, ValueError):
        return None


def record_version(d: Path, info: dict) -> None:
    try:
        (d / VERSION_FILE).write_text(json.dumps({k: info[k] for k in ("sha", "date") if k in info}))
    except OSError:
        pass


def _version_file(d: Path) -> str:
    try:
        return (d / "VERSION").read_text().strip()[:40]
    except OSError:
        return ""


# -- checks ----------------------------------------------------------------------
def _check_git(repo: str, branch: str, g: dict) -> dict:
    want = g["branch"] or branch
    try:
        latest = latest_commit(repo, want)
    except urllib.error.HTTPError as e:
        if e.code not in (404, 422) or want == branch:
            raise
        want, latest = branch, latest_commit(repo, branch)   # branch not on GitHub: use the default
    out = {"method": "git", "latest": latest, "branch": want,
           "installed": {"sha": g["sha"], "branch": g["branch"], "dirty": g["dirty"]}}
    if g["sha"] == latest["sha"]:
        return {**out, "status": "up_to_date", "behind_by": 0, "ahead_by": 0}
    try:
        cmp = _api(f"repos/{repo}/compare/{g['sha']}...{latest['sha']}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {**out, "status": "not_on_github"}
        raise
    behind, ahead = cmp.get("ahead_by", 0), cmp.get("behind_by", 0)
    status = {"identical": "up_to_date", "ahead": "available", "behind": "ahead",
              "diverged": "diverged"}.get(cmp.get("status"), "available")
    return {**out, "status": status, "behind_by": behind, "ahead_by": ahead}


def _check_files(repo: str, branch: str, d: Path) -> dict:
    commits = recent_commits(repo, branch)
    latest = commits[0]
    out = {"method": "files", "latest": latest, "branch": branch, "installed": {}}
    changed = diff_against(d, remote_tree(repo, latest["tree"]))
    if not changed:
        record_version(d, latest)
        return {**out, "status": "up_to_date", "behind_by": 0,
                "installed": {"sha": latest["sha"], "matched": "files"}}
    # Which commit is installed? Try the one recorded at install time, then recent ones.
    rec = recorded(d)
    candidates = [c for c in commits[1:]]
    if rec and rec["sha"] not in {c["sha"] for c in candidates}:
        try:
            info = _api(f"repos/{repo}/commits/{rec['sha']}")
            candidates.insert(0, _commit_info(info))
        except Exception:                                   # noqa: BLE001
            pass
    for c in candidates:
        try:
            if not diff_against(d, remote_tree(repo, c["tree"])):
                record_version(d, c)
                behind = next((i for i, x in enumerate(commits) if x["sha"] == c["sha"]), None)
                if behind is None:
                    cmp = _api(f"repos/{repo}/compare/{c['sha']}...{latest['sha']}")
                    behind = cmp.get("ahead_by")
                return {**out, "status": "available", "behind_by": behind,
                        "installed": {"sha": c["sha"], "matched": "files", "date": c["date"],
                                      "message": c["message"]}}
        except urllib.error.HTTPError:
            break                                            # rate limited: stop trying
    return {**out, "status": "available", "changed_count": len(changed),
            "changed_files": changed[:12],
            "installed": {"sha": rec["sha"] if rec else None, "matched": "none"}}


def check(repo: str, branch: str, d: Path | None) -> dict:
    """{'status': 'up_to_date' | 'available' | 'ahead' | 'diverged' | 'not_on_github'
    | 'not_installed' | 'error', 'method', 'installed', 'latest', 'behind_by', ...}"""
    try:
        if d is None or not d.exists():
            return {"status": "not_installed", "latest": latest_commit(repo, branch)}
        g = git_state(d)
        out = _check_git(repo, branch, g) if g else _check_files(repo, branch, d)
        if v := _version_file(d):
            out["installed"]["version"] = v
        return {**out, "checked": time.time()}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"status": "error", "error": f"{repo} is not published on GitHub yet"}
        if e.code == 403:
            return {"status": "error", "error": "GitHub rate limit reached: try again later, or set GITHUB_TOKEN"}
        return {"status": "error", "error": f"GitHub answered {e.code}"}
    except NotOnGitHub as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:                                   # noqa: BLE001
        return {"status": "error", "error": f"cannot check: {e}"}
