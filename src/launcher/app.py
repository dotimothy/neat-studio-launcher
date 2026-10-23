"""Neat Studio Launcher: Flask + Socket.IO app."""
from __future__ import annotations

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request
from flask_socketio import SocketIO, join_room, leave_room

import registry
import sysinfo
import updates
from procman import Manager, strip_ansi
from terminal import Session

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
VERSION = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else "dev"
LAUNCHER_REPO = os.environ.get("NEAT_LAUNCHER_REPO", "dotimothy/neat-studio-launcher")
LAUNCHER_BRANCH = os.environ.get("NEAT_LAUNCHER_BRANCH", "main")


def create_app(studios=None, logs_dir=None, start_monitor=True):
    app = Flask(__name__, static_folder=str(HERE / "static"),
                template_folder=str(HERE / "templates"))
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 300
    sio = SocketIO(app, async_mode="threading",
                   ping_interval=20, ping_timeout=30)

    studios = studios if studios is not None else registry.load()
    by_id = {s.id: s for s in studios}
    mgr = Manager(studios, logs_dir or (ROOT / "logs"), emit=sio.emit)
    disk_path = studios[0].install_root if studios else "/"
    terms: dict[str, Session] = {}
    launcher_job = {"running": False, "log": ""}

    def info():
        return sysinfo.collect(disk_path)

    def payload():
        snaps = {s["id"]: s for s in mgr.snapshots()}
        return [{**s.public(), **snaps[s.id]} for s in studios]

    def err(msg, code=400):
        return jsonify({"ok": False, "error": str(msg)}), code

    def studio_or_404(sid):
        if sid not in by_id:
            return None
        return by_id[sid]

    # -- pages ---------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("index.html", version=VERSION, studios=[s.public() for s in studios])

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "app": "neat-studio-launcher", "version": VERSION})

    # -- studios ---------------------------------------------------------------
    @app.get("/api/studios")
    def api_studios():
        return jsonify({"studios": payload()})

    @app.post("/api/studios/<sid>/<action>")
    def api_action(sid, action):
        s = studio_or_404(sid)
        if s is None:
            return err("unknown studio", 404)
        body = request.get_json(silent=True) or {}
        try:
            if action == "install":
                mgr.install(sid)
            elif action == "launch":
                mgr.launch(sid, mode=body.get("mode", "exclusive"))
            elif action == "stop":
                mgr.stop(sid)
            elif action == "kill":
                mgr.stop(sid, force=True)
            elif action == "restart":
                mgr.restart(sid)
            elif action == "update":
                mgr.update(sid)
            elif action == "uninstall":
                if body.get("confirm") != sid:
                    return err("confirm by sending the studio id")
                threading.Thread(target=mgr.uninstall, args=(sid,), daemon=True).start()
            elif action == "cancel":
                mgr.cancel_job(sid)
            elif action == "dismiss":
                mgr.clear_error(sid)
            else:
                return err("unknown action", 404)
        except Exception as e:                                 # noqa: BLE001
            return err(e, 409)
        return jsonify({"ok": True, "studio": mgr.snapshot(sid)})

    @app.get("/api/studios/<sid>/log")
    def api_log(sid):
        if sid not in by_id:
            return err("unknown studio", 404)
        text = strip_ansi(mgr.rt[sid].log.text()).replace("\r\n", "\n")
        return Response(text, mimetype="text/plain", headers={
            "Content-Disposition": f'attachment; filename="{sid}.log"'})

    @app.post("/api/studios/<sid>/log/clear")
    def api_log_clear(sid):
        if sid not in by_id:
            return err("unknown studio", 404)
        mgr.rt[sid].log.clear()
        return jsonify({"ok": True})

    @app.get("/api/studios/<sid>/updates")
    def api_updates_one(sid):
        s = studio_or_404(sid)
        if s is None:
            return err("unknown studio", 404)
        if request.args.get("force"):
            updates._cache.pop(f"{s.repo}@{s.branch}", None)
        return jsonify(updates.check(s.repo, s.branch, s.find_dir()))

    @app.get("/api/updates")
    def api_updates():
        if request.args.get("force"):
            updates._cache.clear()
        jobs = {s.id: (s.repo, s.branch, s.find_dir()) for s in studios}
        jobs["launcher"] = (LAUNCHER_REPO, LAUNCHER_BRANCH, ROOT)
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {k: ex.submit(updates.check, *v) for k, v in jobs.items()}
            return jsonify({k: f.result() for k, f in futs.items()})

    @app.post("/api/procs/<int:pid>/kill")
    def api_kill_pid(pid):
        try:
            mgr.kill_pid(pid)
        except Exception as e:                                 # noqa: BLE001
            return err(e, 409)
        return jsonify({"ok": True})

    # -- system ----------------------------------------------------------------
    @app.get("/api/system")
    def api_system():
        return jsonify({**info(), "version": VERSION})

    @app.post("/api/system/reset-mla")
    def api_reset_mla():
        try:
            mgr.reset_mla()
        except Exception as e:                                 # noqa: BLE001
            return err(f"MLA reset failed: {e}. Run it from the Shell tab: "
                       "sudo systemctl restart simaai-appcomplex.service", 500)
        return jsonify({"ok": True})

    @app.post("/api/launcher/update")
    def api_launcher_update():
        if launcher_job["running"]:
            return err("an update is already running", 409)
        launcher_job.update(running=True, log="")

        def work():
            r = subprocess.run(["bash", str(ROOT / "run.sh"), "update"], cwd=str(ROOT),
                               capture_output=True, text=True, stdin=subprocess.DEVNULL)
            launcher_job.update(running=False, log=strip_ansi(r.stdout + r.stderr)[-4000:],
                                code=r.returncode)
            sio.emit("launcher_updated", {"code": r.returncode, "log": launcher_job["log"]})
        threading.Thread(target=work, daemon=True).start()
        return jsonify({"ok": True})

    # -- sockets ---------------------------------------------------------------
    @sio.on("connect")
    def on_connect():
        sio.emit("studios", mgr.snapshots(), to=request.sid)
        sio.emit("system", info(), to=request.sid)

    @sio.on("subscribe_log")
    def on_sub(data):
        sid = (data or {}).get("id")
        if sid in by_id:
            join_room(f"log:{sid}")
            sio.emit("log_init", {"id": sid, "data": mgr.rt[sid].log.text()}, to=request.sid)

    @sio.on("unsubscribe_log")
    def on_unsub(data):
        sid = (data or {}).get("id")
        if sid in by_id:
            leave_room(f"log:{sid}")

    @sio.on("open", namespace="/term")
    def term_open(data):
        sid = request.sid
        old = terms.pop(sid, None)
        if old:
            old.close()
        data = data or {}

        def out(text, sid=sid):
            sio.emit("output", text, to=sid, namespace="/term")

        def done(code, sid=sid):
            terms.pop(sid, None)
            sio.emit("exit", {"code": code}, to=sid, namespace="/term")

        cwd = data.get("cwd")
        if cwd in by_id:
            cwd = str(by_id[cwd].install_dir())
        if not cwd or not os.path.isdir(cwd):
            cwd = None
        terms[sid] = Session(out, done, cols=int(data.get("cols", 120)),
                             rows=int(data.get("rows", 32)), cwd=cwd)

    @sio.on("input", namespace="/term")
    def term_input(data):
        t = terms.get(request.sid)
        if not t:
            return
        if isinstance(data, dict):
            t.write_seq(int(data.get("s", 0)), str(data.get("d", "")))
        elif isinstance(data, str):
            t.write(data)

    @sio.on("resize", namespace="/term")
    def term_resize(data):
        t = terms.get(request.sid)
        if t and data:
            t.resize(int(data.get("cols", 120)), int(data.get("rows", 32)))

    @sio.on("disconnect", namespace="/term")
    def term_disconnect(*_):
        t = terms.pop(request.sid, None)
        if t:
            t.close()

    if start_monitor:
        mgr.start(sysinfo=info)
    app.extensions["launcher"] = {"manager": mgr, "socketio": sio, "terms": terms}
    return app, sio
