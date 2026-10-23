"""Unit tests for the launcher: registry, process manager, terminal, updates, API.

No board needed: studios are replaced by tests/python/fixtures/fake_studio.py.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import procman
import registry
import updates
from terminal import Session

FIX = Path(__file__).resolve().parent / "fixtures"


def wait_for(cond, timeout=10.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return False


class FakeBoard(unittest.TestCase):
    """A temp install root holding fake studios."""

    installed = ("dino", "change", "decision")

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="neat-launcher-test-"))
        self.logs = self.root / "_logs"
        os.environ["NEAT_STUDIOS_ROOT"] = str(self.root)
        self.studios = registry.load(override=FIX / "fake_studios.yaml")
        for s in self.studios:
            if s.id in self.installed:
                d = self.root / s.dirs[0]
                d.mkdir(parents=True)
                shutil.copy(FIX / "fake_studio.py", d / "fake.py")
        self.events = []
        self.mgr = procman.Manager(self.studios, self.logs,
                                   emit=lambda *a, **k: self.events.append(a[0]))

    def tearDown(self):
        for sid in self.mgr.order:
            self.mgr.scan()
            if self.mgr.is_running(sid):
                self.mgr.stop(sid, wait=True, force=True)
        os.environ.pop("NEAT_STUDIOS_ROOT", None)
        shutil.rmtree(self.root, ignore_errors=True)

    def refresh(self, sid):
        self.mgr.scan()
        if self.mgr.is_running(sid):
            self.mgr.check_health(sid)
        return self.mgr.state(sid)


class RegistryTests(unittest.TestCase):
    def test_default_registry_has_four_studios(self):
        ids = [s.id for s in registry.load()]
        self.assertEqual(ids, ["genai", "decision", "change", "dino"])

    def test_ports_are_unique(self):
        ports = [s.port for s in registry.load()]
        self.assertEqual(len(ports), len(set(ports)))

    def test_override_merges_by_id(self):
        studios = {s.id: s for s in registry.load(override=FIX / "fake_studios.yaml")}
        self.assertEqual(studios["dino"].port, 18092)
        self.assertEqual(studios["dino"].repo, "dotimothy/neat-dino-studio")   # kept

    def test_decision_found_in_legacy_dir(self):
        root = Path(tempfile.mkdtemp())
        try:
            (root / "laya" / "webapp").mkdir(parents=True)
            (root / "laya" / "webapp" / "server.py").write_text("")
            with mock.patch.dict(os.environ, {"NEAT_STUDIOS_ROOT": str(root)}):
                s = {x.id: x for x in registry.load()}["decision"]
            self.assertEqual(s.install_dir(), root / "laya")
            self.assertTrue(s.is_installed())
            self.assertIn(f"{root}/laya/webapp/server.py", s.process_patterns())
        finally:
            shutil.rmtree(root)


class ManagerTests(FakeBoard):
    def test_states_before_launch(self):
        self.assertEqual(self.refresh("dino"), "installed")
        self.assertEqual(self.refresh("genai"), "not_installed")

    def test_launch_runs_and_becomes_healthy(self):
        self.mgr.launch("dino")
        self.assertTrue(wait_for(lambda: self.refresh("dino") == "running"))
        snap = self.mgr.snapshot("dino")
        self.assertTrue(snap["owned"])
        self.assertGreaterEqual(len(snap["procs"]), 1)

    def test_exclusive_launch_stops_the_other_studio(self):
        self.mgr.launch("dino")
        self.assertTrue(wait_for(lambda: self.refresh("dino") == "running"))
        self.mgr.launch("change", mode="exclusive")
        self.assertTrue(wait_for(lambda: self.refresh("change") == "running", 15))
        self.assertEqual(self.refresh("dino"), "installed")

    def test_alongside_launch_keeps_both(self):
        self.mgr.launch("dino")
        self.assertTrue(wait_for(lambda: self.refresh("dino") == "running"))
        self.mgr.launch("change", mode="alongside")
        self.assertTrue(wait_for(lambda: self.refresh("change") == "running"))
        self.assertEqual(self.refresh("dino"), "running")

    def test_stop_escalates_to_sigkill(self):
        with mock.patch.object(procman, "STOP_GRACE", 1.0):
            self.mgr.launch("decision")          # ignores SIGTERM
            self.assertTrue(wait_for(lambda: self.refresh("decision") == "running"))
            self.mgr.stop("decision", wait=True)
        self.assertEqual(self.refresh("decision"), "installed")

    def test_force_kill(self):
        self.mgr.launch("dino")
        self.assertTrue(wait_for(lambda: self.refresh("dino") == "running"))
        self.mgr.stop("dino", wait=True, force=True)
        self.assertTrue(wait_for(lambda: self.refresh("dino") == "installed", 5))

    def test_adopts_a_studio_started_elsewhere(self):
        import subprocess
        d = self.root / "neat-change-studio"
        p = subprocess.Popen(["python3", str(d / "fake.py"), "18098"], start_new_session=True)
        try:
            self.assertTrue(wait_for(lambda: self.refresh("change") == "running"))
            self.assertFalse(self.mgr.snapshot("change")["owned"])
            self.mgr.stop("change", wait=True)
            self.assertIsNotNone(p.poll())
        finally:
            if p.poll() is None:
                p.kill()

    def test_install_runs_setup_and_logs(self):
        d = self.root / "neat-genai-studio"
        d.mkdir()
        shutil.copy(FIX / "fake_studio.py", d / "fake.py")        # skip the download
        with mock.patch.object(updates, "latest_commit", return_value={"sha": "abc", "date": "2026-01-01T00:00:00Z"}):
            self.mgr.install("genai")
            self.assertEqual(self.mgr.state("genai"), "installing")
            self.assertTrue(wait_for(lambda: self.mgr.rt["genai"].job.done, 10))
        self.assertEqual(self.mgr.rt["genai"].job.code, 0)
        self.mgr.tail_once()
        self.assertIn("fake setup", self.mgr.rt["genai"].log.text())
        self.assertEqual(updates.recorded(d)["sha"], "abc")

    def test_failed_install_sets_error(self):
        s = self.mgr.studios["genai"]
        s.setup = "echo boom; exit 3"
        d = self.root / "neat-genai-studio"
        d.mkdir()
        (d / "fake.py").write_text("")
        self.mgr.install("genai")
        self.assertTrue(wait_for(lambda: self.mgr.rt["genai"].job.done, 10))
        self.assertEqual(self.mgr.state("genai"), "error")

    def test_kill_pid_refuses_foreign_processes(self):
        self.mgr.scan()
        with self.assertRaises(RuntimeError):
            self.mgr.kill_pid(os.getpid())

    def test_uninstall_refuses_install_root(self):
        s = self.mgr.studios["dino"]
        s.dirs = [str(self.root)]
        (self.root / s.marker).write_text("")
        with self.assertRaises(RuntimeError):
            self.mgr.uninstall("dino")


class LogBufferTests(unittest.TestCase):
    def test_caps_size(self):
        b = procman.LogBuffer(max_chars=100)
        for _ in range(50):
            b.append("x" * 10)
        self.assertLessEqual(len(b.text()), 110)

    def test_strip_ansi(self):
        self.assertEqual(procman.strip_ansi("\x1b[1;32mok\x1b[0m"), "ok")


class TerminalTests(unittest.TestCase):
    def test_shell_echo_and_exit(self):
        out, exited = [], []
        s = Session(out.append, exited.append, shell="/bin/sh")
        s.write("echo neat-$((40+2))\n")
        self.assertTrue(wait_for(lambda: "neat-42" in "".join(out)))
        s.write("exit\n")
        self.assertTrue(wait_for(lambda: exited, 5))


class UpdateTests(unittest.TestCase):
    """Commit-hash update checks, with GitHub replaced by fixed answers."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "run.sh").write_text("echo run\n")
        (self.d / "sub").mkdir()
        (self.d / "sub" / "a.txt").write_text("hello\n")
        updates._cache.clear(); updates._trees.clear()

    def tearDown(self):
        shutil.rmtree(self.d)

    def sha(self, text):
        import hashlib
        b = text.encode()
        return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()

    def commit(self, n, files):
        return {"sha": f"c{n}", "tree": f"t{n}", "date": "2026-01-01T00:00:00Z", "message": f"commit {n}"}, \
            {p: (self.sha(t), "100644") for p, t in files.items()}

    def run_check(self, commits):
        infos = [c for c, _ in commits]
        trees = {c["tree"]: t for c, t in commits}
        with mock.patch.object(updates, "recent_commits", return_value=infos), \
             mock.patch.object(updates, "remote_tree", side_effect=lambda repo, t: trees[t]):
            return updates.check("x/y", "main", self.d)

    def test_blob_sha_matches_git(self):
        import subprocess
        if not shutil.which("git"):
            self.skipTest("git not installed")
        want = subprocess.run(["git", "hash-object", str(self.d / "sub" / "a.txt")], capture_output=True, text=True).stdout.strip()
        self.assertEqual(updates.blob_sha(self.d / "sub" / "a.txt"), want)

    def test_identical_files_are_up_to_date_and_recorded(self):
        r = self.run_check([self.commit(2, {"run.sh": "echo run\n", "sub/a.txt": "hello\n"})])
        self.assertEqual(r["status"], "up_to_date")
        self.assertEqual(r["method"], "files")
        self.assertEqual(updates.recorded(self.d)["sha"], "c2")

    def test_older_copy_counts_commits_behind(self):
        r = self.run_check([
            self.commit(3, {"run.sh": "echo run v3\n", "sub/a.txt": "hello\n"}),
            self.commit(2, {"run.sh": "echo run v2\n", "sub/a.txt": "hello\n"}),
            self.commit(1, {"run.sh": "echo run\n", "sub/a.txt": "hello\n"}),
        ])
        self.assertEqual(r["status"], "available")
        self.assertEqual(r["behind_by"], 2)
        self.assertEqual(r["installed"]["sha"], "c1")

    def test_unknown_copy_lists_changed_files(self):
        r = self.run_check([self.commit(2, {"run.sh": "echo other\n", "sub/a.txt": "hello\n", "new.txt": "x"})])
        self.assertEqual(r["status"], "available")
        self.assertEqual(r["changed_count"], 2)
        self.assertEqual(r["changed_files"], ["new.txt", "run.sh"])

    def test_export_ignored_files_are_skipped(self):
        (self.d / ".gitattributes").write_text("docs/ export-ignore\n*.gif export-ignore\n")
        r = self.run_check([self.commit(2, {"run.sh": "echo run\n", "sub/a.txt": "hello\n",
                                             ".gitattributes": "docs/ export-ignore\n*.gif export-ignore\n",
                                             "docs/big.gif": "gif", "x.gif": "gif"})])
        self.assertEqual(r["status"], "up_to_date")

    def test_git_checkout_uses_compare(self):
        g = {"sha": "local", "branch": "feature/x", "dirty": False}
        latest = {"sha": "remote", "tree": "t", "date": "2026-01-01T00:00:00Z", "message": "m"}
        with mock.patch.object(updates, "git_state", return_value=g), \
             mock.patch.object(updates, "latest_commit", return_value=latest) as lc, \
             mock.patch.object(updates, "_api", return_value={"status": "ahead", "ahead_by": 3, "behind_by": 0}):
            r = updates.check("x/y", "main", self.d)
        lc.assert_called_with("x/y", "feature/x")
        self.assertEqual((r["status"], r["behind_by"], r["method"]), ("available", 3, "git"))

    def test_git_checkout_ahead_is_not_an_update(self):
        g = {"sha": "local", "branch": "main", "dirty": True}
        latest = {"sha": "remote", "tree": "t", "date": "2026-01-01T00:00:00Z", "message": "m"}
        with mock.patch.object(updates, "git_state", return_value=g), \
             mock.patch.object(updates, "latest_commit", return_value=latest), \
             mock.patch.object(updates, "_api", return_value={"status": "behind", "ahead_by": 0, "behind_by": 2}):
            r = updates.check("x/y", "main", self.d)
        self.assertEqual((r["status"], r["ahead_by"]), ("ahead", 2))
        self.assertTrue(r["installed"]["dirty"])

    def test_version_file_is_reported(self):
        (self.d / "VERSION").write_text("0.2.0\n")
        r = self.run_check([self.commit(2, {"run.sh": "echo run\n"})])
        self.assertEqual(r["installed"]["version"], "0.2.0")

    def test_offline(self):
        with mock.patch.object(updates, "recent_commits", side_effect=OSError("no network")):
            self.assertEqual(updates.check("x/y", "main", self.d)["status"], "error")


class ApiTests(FakeBoard):
    def setUp(self):
        super().setUp()
        import app as app_module
        self.app, self.sio = app_module.create_app(self.studios, self.logs, start_monitor=False)
        self.client = self.app.test_client()
        self.mgr = self.app.extensions["launcher"]["manager"]

    def test_index_and_health(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertTrue(self.client.get("/health").json["ok"])

    def test_studios_payload(self):
        data = self.client.get("/api/studios").json["studios"]
        self.assertEqual([s["id"] for s in data], ["genai", "decision", "change", "dino"])
        self.assertIn("tagline", data[0])
        self.assertIn("state", data[0])

    def test_launch_and_stop(self):
        r = self.client.post("/api/studios/dino/launch", json={})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(wait_for(lambda: self.refresh("dino") == "running"))
        self.client.post("/api/studios/dino/stop")
        self.assertTrue(wait_for(lambda: self.refresh("dino") == "installed", 15))

    def test_launch_not_installed_is_409(self):
        self.assertEqual(self.client.post("/api/studios/genai/launch").status_code, 409)

    def test_uninstall_needs_confirmation(self):
        self.assertEqual(self.client.post("/api/studios/dino/uninstall", json={}).status_code, 400)

    def test_unknown(self):
        self.assertEqual(self.client.post("/api/studios/nope/launch").status_code, 404)
        self.assertEqual(self.client.post("/api/studios/dino/explode").status_code, 404)

    def test_system(self):
        data = self.client.get("/api/system").json
        self.assertIn("cpu", data)
        self.assertIn("mem_total", data)


class AppErrorTests(unittest.TestCase):
    def test_reads_top_level_error(self):
        self.assertEqual(procman._app_error(b'{"error": "CywsError: no MLA\\nmore"}'), "CywsError: no MLA")

    def test_ignores_null_empty_and_non_json(self):
        for body in (b'{"error": null}', b'{"error": ""}', b"ok", b"[1]"):
            self.assertEqual(procman._app_error(body), "")


class OrderedInputTests(unittest.TestCase):
    def test_out_of_order_chunks_are_reordered(self):
        out = []
        s = Session(out.append, lambda c: None, shell="/bin/sh")
        try:
            s.write_seq(1, "o ord")
            s.write_seq(2, "ered\n")
            s.write_seq(0, "ech")
            self.assertTrue(wait_for(lambda: "".join(out).count("ordered") >= 2))
        finally:
            s.close()


class InstallMarkerTests(FakeBoard):
    def test_setup_that_leaves_no_marker_is_an_error(self):
        s = self.mgr.studios["genai"]
        s.installed_marker = "never-created"
        d = self.root / "neat-genai-studio"
        d.mkdir()
        shutil.copy(FIX / "fake_studio.py", d / "fake.py")
        with mock.patch.object(updates, "latest_commit", return_value={"sha": "x", "date": "2026-01-01T00:00:00Z"}):
            self.mgr.install("genai")
            self.assertTrue(wait_for(lambda: self.mgr.rt["genai"].job.done, 10))
        self.assertEqual(self.mgr.state("genai"), "error")
        self.assertIn("never-created", self.mgr.rt["genai"].error)


class IndexTemplateTests(FakeBoard):
    def test_intro_lists_every_studio(self):
        import app as app_module
        app, _ = app_module.create_app(self.studios, self.logs, start_monitor=False)
        html = app.test_client().get("/").get_data(as_text=True)
        self.assertEqual(html.count('class="intro-studio"'), 4)
        for s in self.studios:
            self.assertIn(s.name, html)
