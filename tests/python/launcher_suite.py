"""Unit tests for the launcher: registry, process manager, terminal, updates, API.

No board needed: studios are replaced by tests/python/fixtures/fake_studio.py.
"""
from __future__ import annotations

import os
import shutil
import signal
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import procman
import psutil
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

    def test_local_edits_on_the_latest_commit_are_not_an_update(self):
        updates.record_version(self.d, {"sha": "c2"})
        r = self.run_check([self.commit(2, {"run.sh": "echo other\n", "sub/a.txt": "hello\n"})])
        self.assertEqual((r["status"], r["changed_count"]), ("modified", 1))

    def test_export_ignored_files_are_skipped(self):
        (self.d / ".gitattributes").write_text("docs/ export-ignore\n*.gif export-ignore\n")
        r = self.run_check([self.commit(2, {"run.sh": "echo run\n", "sub/a.txt": "hello\n",
                                             ".gitattributes": "docs/ export-ignore\n*.gif export-ignore\n",
                                             "docs/big.gif": "gif", "x.gif": "gif"})])
        self.assertEqual(r["status"], "up_to_date")

    def test_git_checkout_counts_commits_behind(self):
        g = {"sha": "local", "branch": "feature/x", "dirty": False}
        latest = {"sha": "remote", "tree": "t", "date": "2026-01-01T00:00:00Z", "message": "m"}
        with mock.patch.object(updates, "git_state", return_value=g), \
             mock.patch.object(updates, "git_compare", return_value=(latest, 0, 3)) as gc:
            r = updates.check("x/y", "main", self.d)
        self.assertEqual(gc.call_args[0][1], "feature/x")
        self.assertEqual((r["status"], r["behind_by"], r["method"]), ("available", 3, "git"))

    def test_git_checkout_ahead_is_not_an_update(self):
        g = {"sha": "local", "branch": "main", "dirty": True}
        latest = {"sha": "remote", "tree": "t", "date": "2026-01-01T00:00:00Z", "message": "m"}
        with mock.patch.object(updates, "git_state", return_value=g), \
             mock.patch.object(updates, "git_compare", return_value=(latest, 2, 0)):
            r = updates.check("x/y", "main", self.d)
        self.assertEqual((r["status"], r["ahead_by"]), ("ahead", 2))
        self.assertTrue(r["installed"]["dirty"])

    def test_git_checkout_on_unpublished_branch_falls_back(self):
        g = {"sha": "local", "branch": "local-only", "dirty": False}
        latest = {"sha": "local", "tree": "t", "date": "x", "message": "m"}
        calls = []
        def fake(d, branch, repo):
            calls.append(branch)
            if branch == "local-only":
                raise updates.NotOnGitHub("that branch is not on GitHub")
            return latest, 0, 0
        with mock.patch.object(updates, "git_state", return_value=g), mock.patch.object(updates, "git_compare", side_effect=fake):
            r = updates.check("x/y", "main", self.d)
        self.assertEqual(calls, ["local-only", "main"])
        self.assertEqual(r["status"], "up_to_date")

    def test_real_git_mirror(self):
        """End to end against a local bare repo standing in for GitHub."""
        import subprocess
        if not shutil.which("git"):
            self.skipTest("git not installed")
        tmp = Path(tempfile.mkdtemp())
        try:
            src = tmp / "src"; src.mkdir()
            run = lambda *a, cwd=src: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)
            run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
            (src / "run.sh").write_text("v1\n"); run("add", "."); run("commit", "-qm", "one")
            (src / "run.sh").write_text("v2\n"); run("commit", "-qam", "two")
            (src / "run.sh").write_text("v3\n"); run("commit", "-qam", "three")
            with mock.patch.object(updates, "_url", return_value=str(src)), \
                 mock.patch.object(updates, "CACHE_DIR", tmp / "cache"):
                updates._cache.clear(); updates._fetched.clear(); updates._trees.clear()
                inst = tmp / "inst"; inst.mkdir(); (inst / "run.sh").write_text("v1\n")
                r = updates.check("x/y", "main", inst)
                self.assertEqual((r["status"], r["behind_by"], r["method"]), ("available", 2, "files"))
                (inst / "run.sh").write_text("v3\n")
                r = updates.check("x/y", "main", inst)
                self.assertEqual(r["status"], "up_to_date")
        finally:
            shutil.rmtree(tmp)

    def test_version_file_is_reported(self):
        (self.d / "VERSION").write_text("0.2.0\n")
        r = self.run_check([self.commit(2, {"run.sh": "echo run\n"})])
        self.assertEqual(r["installed"]["version"], "0.2.0")

    def test_offline(self):
        with mock.patch.object(updates, "recent_commits", side_effect=RuntimeError("Could not resolve host: github.com")):
            r = updates.check("x/y", "main", self.d)
        self.assertEqual(r["status"], "error")
        self.assertTrue(r["offline"])


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


class BranchTests(FakeBoard):
    def capture(self, sid, branch=None):
        seen = {}
        with mock.patch.object(self.mgr, "_run_job", side_effect=lambda sid, kind, script, cwd, after=None: seen.update(script=script, after=after)):
            self.mgr.install(sid, branch)
        return seen

    def test_default_install_uses_main(self):
        s = self.capture("genai")["script"]
        self.assertIn("archive/refs/heads/main.tar.gz", s)
        self.assertIn("export STUDIO_BRANCH=main", s)

    def test_install_from_branch(self):
        s = self.capture("genai", "feature/multi-model-studio")["script"]
        self.assertIn("archive/refs/heads/feature/multi-model-studio.tar.gz", s)
        self.assertIn("export STUDIO_BRANCH=feature/multi-model-studio", s)

    def test_switching_an_installed_copy_downloads_over_it(self):
        s = self.capture("dino", "grounding-dino-tiny")["script"]
        self.assertIn("over the installed copy", s)
        self.assertNotIn("if [ ! -f", s)

    def test_switching_a_git_checkout_uses_git(self):
        (self.root / "neat-dino-studio" / ".git").mkdir()
        s = self.capture("dino", "dev")["script"]
        self.assertIn("checkout", s)
        self.assertNotIn("curl", s)

    def test_bad_branch_names_are_refused(self):
        for bad in ("../x", "-rf", "a b", "x;rm -rf /", "feat/", "a..b", ""):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                self.mgr.install("dino", bad or " ")

    def test_recorded_branch_drives_update_checks(self):
        d = self.root / "neat-dino-studio"
        updates.record_version(d, {"sha": "abc", "date": "x"}, "grounding-dino-tiny")
        self.assertEqual(updates.installed_branch(d, "main"), "grounding-dino-tiny")
        self.mgr._branch_cache.clear()
        self.assertEqual(self.mgr.branch_of("dino"), "grounding-dino-tiny")
        seen = {}
        with mock.patch.object(updates, "_check_files", side_effect=lambda repo, branch, d: seen.setdefault("b", branch) and {"installed": {}}):
            updates.check("x/y", "main", d)
        self.assertEqual(seen["b"], "grounding-dino-tiny")
        # A later record without a branch keeps the branch.
        updates.record_version(d, {"sha": "def"})
        self.assertEqual(updates.recorded(d)["branch"], "grounding-dino-tiny")


class RegistryTextTests(unittest.TestCase):
    def test_every_visible_field_is_text(self):
        """A stray ': ' in studios.yaml turns a bullet into a mapping ([object Object])."""
        for s in registry.load():
            for b in s.bullets:
                self.assertIsInstance(b, str, f"{s.id} bullet {b!r}")
            for v in (s.tagline, s.pitch, s.category, s.name):
                self.assertIsInstance(v, str, s.id)
            for k, v in s.metric.items():
                self.assertIsInstance(v, str, f"{s.id} metric {k}")
            for g in s.gallery:
                self.assertIsInstance(g["title"], str, s.id)
                self.assertIsInstance(g["caption"], str, s.id)


class OfflineTests(unittest.TestCase):
    def test_offline_keeps_last_good_result(self):
        import urllib.error
        d = Path(tempfile.mkdtemp())
        try:
            good = {"status": "up_to_date", "method": "files", "installed": {}, "latest": {"sha": "a"}}
            with mock.patch.object(updates, "_check", return_value=good):
                updates.check("x/y", "main", d)
            limited = {"status": "error", "offline": True, "error": "cannot reach GitHub"}
            with mock.patch.object(updates, "_check", return_value=limited):
                r = updates.check("x/y", "main", d)
            self.assertEqual(r["status"], "up_to_date")
            self.assertTrue(r["stale"])
        finally:
            shutil.rmtree(d)

    def test_ls_remote_parsing(self):
        out = "ref: refs/heads/dev\tHEAD\nabc\tHEAD\n111\trefs/heads/dev\n222\trefs/heads/feature/x\n"
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stdout=out, stderr="")):
            r = updates.ls_remote("x/y")
        self.assertEqual(r["default"], "dev")
        self.assertEqual([b["name"] for b in r["branches"]], ["dev", "feature/x"])


class ChildProcessTests(FakeBoard):
    def test_stop_also_stops_child_processes(self):
        import subprocess
        d = self.root / "neat-change-studio"
        # A studio process (started elsewhere, not a group leader) with a child of its own.
        parent = subprocess.Popen(["python3", "-c",
            "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            f"exec(open('{d / 'fake.py'}').read().replace('sys.argv[1]', '18098'))", str(d / "fake.py")])
        try:
            self.assertTrue(wait_for(lambda: self.refresh("change") == "running"))
            kids = psutil.Process(parent.pid).children(recursive=True)
            self.assertTrue(kids)
            self.mgr.stop("change", wait=True)
            self.assertTrue(wait_for(lambda: not any(k.is_running() and k.status() != psutil.STATUS_ZOMBIE for k in kids), 5))
        finally:
            for k in psutil.Process(parent.pid).children(recursive=True) if parent.poll() is None else []:
                k.kill()
            if parent.poll() is None:
                parent.kill()


class CleanStopLogTests(FakeBoard):
    def test_clean_stop_starts_a_fresh_log(self):
        self.mgr.launch("dino")
        self.assertTrue(wait_for(lambda: self.refresh("dino") == "running"))
        self.mgr.tail_once()
        self.assertIn("Ready", self.mgr.rt["dino"].log.text())
        self.mgr.stop("dino", wait=True)
        self.mgr.tail_once()
        text = self.mgr.rt["dino"].log.text()
        self.assertNotIn("Ready", text)
        self.assertIn("Stopped", text)


class SingleSignalTests(FakeBoard):
    def test_each_process_gets_one_sigterm(self):
        """A launched studio is both in the owned group and matched by pattern:
        it must still receive SIGTERM only once."""
        sent = []
        real_killpg, real_send = os.killpg, psutil.Process.send_signal
        self.mgr.launch("dino")
        self.assertTrue(wait_for(lambda: self.refresh("dino") == "running"))
        pids = {p.pid for p in self.mgr.procs("dino")} | {self.mgr.rt["dino"].owned.pid}
        with mock.patch("os.killpg", side_effect=lambda pg, sig: (sent.append(("group", pg)), real_killpg(pg, sig))), \
             mock.patch.object(psutil.Process, "send_signal", autospec=True,
                               side_effect=lambda self_, sig: (sent.append(("pid", self_.pid)), real_send(self_, sig))):
            self.mgr._signal("dino", signal.SIGTERM)
        hits = [x for x in sent if (x[0] == "group" and x[1] == self.mgr.rt["dino"].owned.pid) or (x[0] == "pid" and x[1] in pids)]
        self.assertEqual(len(hits), 1, sent)
