"""Install, launch, monitor and stop studios.

Studios run in their own session (setsid) with output in logs/<id>.log, so
they outlive a launcher restart and are found again by their command line.
Studios started outside the launcher (desktop icon, alias) are found the same
way and can be stopped from here too.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import ssl
import subprocess
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import psutil

from registry import Studio
import updates

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
Emit = Callable[..., None]
STOP_GRACE = float(os.environ.get("NEAT_LAUNCHER_STOP_GRACE", "10"))


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


class LogBuffer:
    """Recent output of one studio as raw text chunks (ANSI kept for xterm)."""

    def __init__(self, max_chars: int = 400_000):
        self.max_chars = max_chars
        self.chunks: deque[str] = deque()
        self.size = 0
        self.lock = threading.Lock()

    def append(self, text: str) -> None:
        with self.lock:
            self.chunks.append(text)
            self.size += len(text)
            while self.size > self.max_chars and len(self.chunks) > 1:
                self.size -= len(self.chunks.popleft())

    def text(self) -> str:
        with self.lock:
            return "".join(self.chunks)

    def clear(self) -> None:
        with self.lock:
            self.chunks.clear()
            self.size = 0


@dataclass
class Job:
    kind: str                      # install | update | uninstall | launch | stop
    started: float = field(default_factory=time.time)
    proc: subprocess.Popen | None = None
    step: str = ""
    done: bool = False
    code: int | None = None
    error: str = ""


@dataclass
class StudioRuntime:
    job: Job | None = None
    owned: subprocess.Popen | None = None
    launched_at: float = 0.0
    stopping: bool = False
    error: str = ""
    healthy: bool = False
    app_error: str = ""
    health_checked: float = 0.0
    log: LogBuffer = field(default_factory=LogBuffer)
    offsets: dict[str, int] = field(default_factory=dict)


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def probe(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    """(answers below 500, the studio's own error message if its JSON has one)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=_ssl_ctx()) as r:
            body = r.read(200_000)
            return r.status < 500, _app_error(body)
    except urllib.error.HTTPError as e:
        return e.code < 500, ""
    except Exception:
        return False, ""


def _app_error(body: bytes) -> str:
    """Studios report a failed model load as a top-level "error" in their status JSON."""
    try:
        data = json.loads(body)
    except Exception:                                          # noqa: BLE001
        return ""
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, str) and err.strip():
        return err.strip().splitlines()[0][:300]
    return ""


class Manager:
    def __init__(self, studios: list[Studio], logs_dir: str | Path,
                 emit: Emit | None = None):
        self.studios = {s.id: s for s in studios}
        self.order = [s.id for s in studios]
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.emit = emit or (lambda *a, **k: None)
        self.rt = {s.id: StudioRuntime() for s in studios}
        self.system_log = LogBuffer()
        self.lock = threading.RLock()
        self._procs: dict[str, list[psutil.Process]] = {s: [] for s in self.studios}
        self._pcache: dict[int, psutil.Process] = {}
        self._stop = threading.Event()

    # -- paths ---------------------------------------------------------------
    def run_log(self, sid: str) -> Path:
        return self.logs_dir / f"{sid}.log"

    def job_log(self, sid: str) -> Path:
        return self.logs_dir / f"{sid}-job.log"

    def _note(self, sid: str, text: str, path: Path | None = None) -> None:
        """Write a launcher line (shown in the studio's log) to a log file."""
        line = f"\x1b[38;2;154;190;30m▸\x1b[0m \x1b[1m{text}\x1b[0m\n"
        with open(path or self.job_log(sid), "a", encoding="utf-8") as f:
            f.write(line)

    # -- process discovery ---------------------------------------------------
    def scan(self) -> None:
        """Find each studio's processes: the owned process group plus any
        process whose command line names one of the studio's scripts."""
        patterns = {sid: s.process_patterns() for sid, s in self.studios.items()}
        owned_groups = {}
        for sid, rt in self.rt.items():
            if rt.owned is not None and rt.owned.poll() is None:
                owned_groups[rt.owned.pid] = sid
        found: dict[str, list[psutil.Process]] = {sid: [] for sid in self.studios}
        seen = set()
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = " ".join(p.info["cmdline"] or [])
                if not cmd:
                    continue
                hit = None
                try:
                    pg = os.getpgid(p.pid)
                except OSError:
                    pg = None
                if pg in owned_groups:
                    hit = owned_groups[pg]
                else:
                    for sid, pats in patterns.items():
                        if any(pat in cmd for pat in pats):
                            hit = sid
                            break
                if hit:
                    proc = self._pcache.get(p.pid)
                    if proc is None:
                        proc = p
                        proc.cpu_percent(None)     # prime
                        self._pcache[p.pid] = proc
                    found[hit].append(proc)
                    seen.add(p.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for pid in list(self._pcache):
            if pid not in seen:
                del self._pcache[pid]
        self._procs = found

    def procs(self, sid: str) -> list[psutil.Process]:
        return [p for p in self._procs.get(sid, []) if p.is_running()]

    def is_running(self, sid: str) -> bool:
        rt = self.rt[sid]
        return bool(self.procs(sid)) or (rt.owned is not None and rt.owned.poll() is None)

    # -- state ---------------------------------------------------------------
    def url(self, sid: str, host: str = "127.0.0.1") -> str:
        s = self.studios[sid]
        return f"{s.scheme}://{host}:{s.port}/"

    def check_health(self, sid: str) -> bool:
        s = self.studios[sid]
        rt = self.rt[sid]
        ok, app_err = probe(f"{s.scheme}://127.0.0.1:{s.port}{s.health}")
        if not ok and s.scheme == "https":
            ok, app_err = probe(f"http://127.0.0.1:{s.port}{s.health}")
        rt.healthy, rt.health_checked, rt.app_error = ok, time.time(), app_err
        return ok

    def state(self, sid: str) -> str:
        s, rt = self.studios[sid], self.rt[sid]
        if rt.job and not rt.job.done and rt.job.kind in ("install", "update", "uninstall"):
            return rt.job.kind.rstrip("e") + "ing"
        if self.is_running(sid):
            if rt.stopping:
                return "stopping"
            return "running" if rt.healthy else "starting"
        if rt.error:
            return "error"
        return "installed" if s.is_installed() else "not_installed"

    def snapshot(self, sid: str) -> dict:
        s, rt = self.studios[sid], self.rt[sid]
        procs = []
        for p in self.procs(sid):
            try:
                with p.oneshot():
                    procs.append({
                        "pid": p.pid,
                        "name": p.name(),
                        "cmd": " ".join(p.cmdline())[:240],
                        "cpu": round(p.cpu_percent(None), 1),
                        "rss": p.memory_info().rss,
                        "started": p.create_time(),
                        "status": p.status(),
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        job = rt.job
        return {
            "id": sid,
            "state": self.state(sid),
            "installed": s.is_installed(),
            "dir": str(s.install_dir()),
            "found_dir": bool(s.find_dir()),
            "owned": rt.owned is not None and rt.owned.poll() is None,
            "healthy": rt.healthy,
            "error": rt.error,
            "app_error": rt.app_error if self.is_running(sid) else "",
            "procs": procs,
            "cpu": round(sum(p["cpu"] for p in procs), 1),
            "rss": sum(p["rss"] for p in procs),
            "uptime": (time.time() - min(p["started"] for p in procs)) if procs else 0,
            "job": None if job is None else {
                "kind": job.kind, "step": job.step, "done": job.done,
                "code": job.code, "error": job.error,
                "elapsed": time.time() - job.started,
            },
        }

    def snapshots(self) -> list[dict]:
        return [self.snapshot(sid) for sid in self.order]

    # -- log tailing ---------------------------------------------------------
    def _tail_paths(self, sid: str) -> list[Path]:
        return [self.job_log(sid), self.run_log(sid), *self.studios[sid].log_paths()]

    def tail_once(self) -> None:
        for sid, rt in self.rt.items():
            for path in self._tail_paths(sid):
                key = str(path)
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                off = rt.offsets.get(key)
                if off is None:
                    off = max(0, size - 32_000)       # first sight: recent history
                if size < off:
                    off = 0                            # truncated / rotated
                if size == off:
                    rt.offsets[key] = off
                    continue
                with open(path, "rb") as f:
                    f.seek(off)
                    data = f.read(min(size - off, 256_000))
                rt.offsets[key] = off + len(data)
                text = data.decode("utf-8", "replace").replace("\r\n", "\n")
                text = text.replace("\n", "\r\n")
                rt.log.append(text)
                if rt.job and not rt.job.done:
                    steps = [ln for ln in strip_ansi(text).splitlines()
                             if ln.strip().startswith(("▸", "==>", "->", "✔"))]
                    if steps:
                        rt.job.step = steps[-1].strip(" ▸✔=->")[:120]
                self.emit("log", {"id": sid, "data": text}, to=f"log:{sid}")

    # -- jobs ----------------------------------------------------------------
    def _env(self, s: Studio, extra: dict | None = None) -> dict:
        env = dict(os.environ)
        env.update({k: str(v) for k, v in s.env.items()})
        env.update({k: str(v) for k, v in (extra or {}).items()})
        env["PYTHONUNBUFFERED"] = "1"
        env.setdefault("TERM", "xterm-256color")
        env.pop("NEAT_LAUNCHER_CONFIG", None)
        return env

    def _busy(self, sid: str) -> bool:
        job = self.rt[sid].job
        return job is not None and not job.done

    def _run_job(self, sid: str, kind: str, script: str, cwd: Path,
                 after: Callable[[int], None] | None = None) -> Job:
        s = self.studios[sid]
        rt = self.rt[sid]
        job = Job(kind=kind)
        rt.job, rt.error = job, ""
        self._note(sid, f"{kind} {s.name} · {time.strftime('%H:%M:%S')}")

        def work():
            log = open(self.job_log(sid), "ab")
            try:
                job.proc = subprocess.Popen(
                    ["bash", "-c", script], cwd=str(cwd), env=self._env(s),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True)
                job.code = job.proc.wait()
            except Exception as e:                         # noqa: BLE001
                job.code, job.error = -1, str(e)
            finally:
                log.close()
            if job.code == 0:
                self._note(sid, f"{kind} finished")
            else:
                job.error = job.error or f"{kind} failed (exit {job.code})"
                rt.error = job.error
                self._note(sid, job.error)
            if after:
                try:
                    after(job.code or 0)
                except Exception as e:                     # noqa: BLE001
                    rt.error = job.error = str(e)
                    self._note(sid, str(e))
            job.done = True
            self.push()

        threading.Thread(target=work, daemon=True, name=f"{kind}-{sid}").start()
        self.push()
        return job

    def install(self, sid: str) -> Job:
        s = self.studios[sid]
        if self._busy(sid):
            raise RuntimeError("a job is already running for this studio")
        d = s.install_dir()
        d.parent.mkdir(parents=True, exist_ok=True)
        q = shlex.quote(str(d))
        url = f"https://github.com/{s.repo}/archive/{s.branch}.tar.gz"
        script = f"""set -eo pipefail
if [ ! -f {q}/{shlex.quote(s.marker)} ]; then
  echo "▸ Downloading {s.repo} ({s.branch})"
  mkdir -p {q}
  curl -fSL --progress-bar {shlex.quote(url)} | tar -xz --strip-components=1 -C {q}
  echo "✔ Source in {d}"
fi
cd {q}
chmod +x ./*.sh webapp/*.sh 2>/dev/null || true
echo "▸ Running setup: {s.setup}"
{s.setup}
"""
        return self._run_job(sid, "install", script, d.parent, after=self._recorder(sid))

    def update(self, sid: str) -> Job:
        s = self.studios[sid]
        if self._busy(sid):
            raise RuntimeError("a job is already running for this studio")
        if not s.update:
            raise RuntimeError("this studio has no update command")
        return self._run_job(sid, "update", s.fmt(s.update), s.install_dir(),
                             after=self._recorder(sid))

    def _recorder(self, sid: str) -> Callable[[int], None]:
        """After a good install/update, remember which commit is on disk."""
        s = self.studios[sid]

        def after(code: int) -> None:
            if code != 0:
                return
            if not s.is_installed():
                where = s.install_dir()
                missing = s.marker if s.find_dir() is None else s.installed_marker
                raise RuntimeError(f"Setup finished, but {missing} was not found in {where}: see the log")
            try:
                updates._cache.pop(f"{s.repo}@{s.branch}", None)
                info = updates.latest_commit(s.repo, s.branch)
                if not (s.install_dir() / ".git").exists():
                    updates.record_version(s.install_dir(), info)
            except Exception:                                  # noqa: BLE001
                pass
        return after

    def uninstall(self, sid: str) -> Job:
        s = self.studios[sid]
        if self._busy(sid):
            raise RuntimeError("a job is already running for this studio")
        d = s.find_dir()
        if d is None:
            raise RuntimeError("not installed")
        if d.resolve() in (Path("/"), Path.home().resolve(), Path(s.install_root).resolve()):
            raise RuntimeError(f"refusing to remove {d}")
        self.stop(sid, wait=True)
        q = shlex.quote(str(d))
        return self._run_job(sid, "uninstall",
                             f'echo "▸ Removing {d}"; rm -rf -- {q} && echo "✔ Removed"',
                             d.parent)

    def cancel_job(self, sid: str) -> None:
        job = self.rt[sid].job
        if job and job.proc and job.proc.poll() is None:
            os.killpg(job.proc.pid, signal.SIGTERM)

    # -- launch / stop ---------------------------------------------------------
    def launch(self, sid: str, mode: str = "exclusive") -> None:
        """Start a studio. mode 'exclusive' first stops every other running
        MLA studio; 'alongside' leaves them up (and sets alongside_env)."""
        s = self.studios[sid]
        rt = self.rt[sid]
        if not s.is_installed():
            raise RuntimeError(f"{s.name} is not installed")
        if self._busy(sid):
            raise RuntimeError("a job is still running for this studio")
        if self.is_running(sid):
            return
        rt.error, rt.stopping, rt.healthy = "", False, False
        rt.job = Job(kind="launch")

        def work():
            try:
                if mode == "exclusive" and s.mla:
                    for other in self.order:
                        if other != sid and self.studios[other].mla and self.is_running(other):
                            self._note(sid, f"Stopping {self.studios[other].name} to free the MLA",
                                       self.run_log(sid))
                            self.stop(other, wait=True)
                extra = s.alongside_env if mode == "alongside" else {}
                self._note(sid, f"Launching {s.name} · {s.launch} · {time.strftime('%H:%M:%S')}",
                           self.run_log(sid))
                log = open(self.run_log(sid), "ab")
                rt.owned = subprocess.Popen(
                    ["bash", "-c", s.fmt(s.launch)], cwd=str(s.install_dir()),
                    env=self._env(s, extra), stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True)
                log.close()
                rt.launched_at = time.time()
                rt.job.done = True
                threading.Thread(target=self._watch_owned, args=(sid, rt.owned),
                                 daemon=True).start()
            except Exception as e:                           # noqa: BLE001
                rt.error = str(e)
                rt.job.done, rt.job.error = True, str(e)
            self.push()

        threading.Thread(target=work, daemon=True, name=f"launch-{sid}").start()
        self.push()

    def _watch_owned(self, sid: str, proc: subprocess.Popen) -> None:
        code = proc.wait()
        rt = self.rt[sid]
        if rt.owned is proc:
            if code not in (0, -signal.SIGTERM, -signal.SIGKILL, 143, 137, 130) and not rt.stopping:
                rt.error = f"exited with code {code}: see the log"
            self._note(sid, f"Process exited ({code})", self.run_log(sid))
            self.push()

    def _signal(self, sid: str, sig: int) -> None:
        rt = self.rt[sid]
        if rt.owned is not None and rt.owned.poll() is None:
            try:
                os.killpg(rt.owned.pid, sig)
            except OSError:
                pass
        for p in self.procs(sid):
            try:
                pg = os.getpgid(p.pid)
                if pg == p.pid:            # a session/group leader: take its group
                    os.killpg(pg, sig)
                else:
                    p.send_signal(sig)
            except (OSError, psutil.Error):
                pass

    def stop(self, sid: str, wait: bool = False, force: bool = False) -> None:
        """Stop gracefully (the studio's stop command or SIGTERM), then SIGKILL
        whatever is left after the grace period. force=True kills at once."""
        s, rt = self.studios[sid], self.rt[sid]

        def work():
            rt.stopping = True
            self.push()
            try:
                if force:
                    self._note(sid, "Force kill (SIGKILL)", self.run_log(sid))
                    self._signal(sid, signal.SIGKILL)
                else:
                    self._note(sid, "Stopping", self.run_log(sid))
                    if s.stop and s.is_installed():
                        subprocess.run(["bash", "-c", s.fmt(s.stop)], cwd=str(s.install_dir()),
                                       env=self._env(s), stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       timeout=STOP_GRACE + 15)
                    self._signal(sid, signal.SIGTERM)
                    deadline = time.time() + STOP_GRACE
                    while time.time() < deadline:
                        self.scan()
                        if not self.is_running(sid):
                            break
                        time.sleep(0.25)
                    self.scan()
                    if self.is_running(sid):
                        self._note(sid, f"Still running after {STOP_GRACE:.0f}s: SIGKILL",
                                   self.run_log(sid))
                        self._signal(sid, signal.SIGKILL)
                time.sleep(0.3)
                self.scan()
            except Exception as e:                               # noqa: BLE001
                rt.error = str(e)
            finally:
                rt.stopping, rt.healthy, rt.error = False, False, rt.error if force else ""
                self.push()

        if wait:
            work()
        else:
            threading.Thread(target=work, daemon=True, name=f"stop-{sid}").start()

    def restart(self, sid: str) -> None:
        def work():
            self.stop(sid, wait=True)
            self.launch(sid, mode="alongside")
        threading.Thread(target=work, daemon=True).start()

    def kill_pid(self, pid: int) -> None:
        """Kill one process, only if it belongs to a studio."""
        for sid in self.order:
            if any(p.pid == pid for p in self.procs(sid)):
                os.kill(pid, signal.SIGKILL)
                return
        raise RuntimeError(f"pid {pid} is not a studio process")

    def clear_error(self, sid: str) -> None:
        self.rt[sid].error = ""
        self.push()

    # -- system --------------------------------------------------------------
    def reset_mla(self, service: str = "simaai-appcomplex.service") -> int:
        cmd = ["sudo", "-n", "systemctl", "restart", service]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        msg = f"$ {' '.join(cmd)}\r\n{(r.stdout + r.stderr).replace(chr(10), chr(13) + chr(10))}"
        self.system_log.append(msg + f"exit {r.returncode}\r\n")
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip() or f"exit {r.returncode}")
        return r.returncode

    # -- loop ----------------------------------------------------------------
    def push(self) -> None:
        self.emit("studios", self.snapshots())

    def loop(self, sysinfo: Callable[[], dict] | None = None, period: float = 2.0) -> None:
        last = 0.0
        while not self._stop.is_set():
            try:
                self.tail_once()
                if time.time() - last >= period:
                    last = time.time()
                    self.scan()
                    for sid in self.order:
                        if self.is_running(sid):
                            self.check_health(sid)
                        else:
                            self.rt[sid].healthy = False
                            self.rt[sid].app_error = ""
                    self.push()
                    if sysinfo:
                        self.emit("system", sysinfo())
            except Exception as e:                       # noqa: BLE001
                self.system_log.append(f"monitor error: {e}\r\n")
            self._stop.wait(0.4)

    def start(self, sysinfo=None) -> threading.Thread:
        self.scan()
        t = threading.Thread(target=self.loop, args=(sysinfo,), daemon=True, name="monitor")
        t.start()
        return t

    def shutdown(self) -> None:
        self._stop.set()
