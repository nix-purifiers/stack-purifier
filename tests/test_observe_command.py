"""Owned synthetic commands only: no Stack build or job controls."""
import importlib.util
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("observe-command.py").resolve()
PREFIX = "[stack-ci-observe] "


def records(stderr):
    return [json.loads(line[len(PREFIX):]) for line in stderr.splitlines()
            if line.startswith(PREFIX)]


def target_stderr(stderr):
    return "".join(line for line in stderr.splitlines(keepends=True)
                   if not line.startswith(PREFIX))


class Lifecycle(unittest.TestCase):
    def launch(self, code, *args, options=(), **kwargs):
        return subprocess.Popen(
            [sys.executable, str(SCRIPT), *options, "--", sys.executable,
             "-c", code, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, **kwargs)

    def test_exec_identity_input_output_environment_and_no_inherited_child(self):
        with tempfile.TemporaryDirectory() as cwd:
            code = ("import json,os,pathlib,sys,time; "
                    "time.sleep(.08); "
                    "print(json.dumps({'pid':os.getpid(),'argv':sys.argv[1:],"
                    "'input':sys.stdin.read(),'cwd':os.getcwd(),"
                    "'cores':os.environ['NIX_BUILD_CORES'],"
                    "'children':pathlib.Path('/proc/self/task/%s/children'"
                    "%os.getpid()).read_text().strip()})); "
                    "print('target stderr',file=sys.stderr)")
            p = self.launch(code, "a b", "--flag", cwd=cwd,
                            env=os.environ | {"NIX_BUILD_CORES": "4"})
            stdout, stderr = p.communicate("target stdin", timeout=5)
            self.assertEqual(p.returncode, 0)
            self.assertEqual(json.loads(stdout), {
                "pid": p.pid, "argv": ["a b", "--flag"], "input": "target stdin",
                "cwd": cwd, "cores": "4", "children": ""})
            self.assertEqual(target_stderr(stderr), "target stderr\n")
            events = records(stderr)
            self.assertEqual(events[0]["interval_seconds"], 60)
            self.assertEqual(events[0]["max_samples"], 120)
            self.assertEqual(events[0]["NIX_BUILD_CORES"], "4")
            self.assertTrue(any(e["event"] == "target-exited" for e in events))
            first_sample = next(e for e in events if e["event"] == "sample")
            self.assertEqual(first_sample["processes"][0]["pid"], p.pid)
            self.assertTrue(all(len(line.encode()) <= 4096 for line in stderr.splitlines()
                                if line.startswith(PREFIX)))

    def test_failure_status_and_output_are_not_replaced_by_observer_success(self):
        p = self.launch("import sys,time; time.sleep(.05); print('stdout'); "
                        "print('stderr',file=sys.stderr); sys.exit(23)")
        stdout, stderr = p.communicate(timeout=5)
        self.assertEqual((p.returncode, stdout, target_stderr(stderr)),
                         (23, "stdout\n", "stderr\n"))
        self.assertIn("target-exited", [e["event"] for e in records(stderr)])

    def test_external_timeout_signal_is_preserved_and_sampler_exits_itself(self):
        p = self.launch("import time; print('ready',flush=True); time.sleep(60)",
                        options=("--interval-seconds", ".02", "--max-samples", "100"))
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                p.communicate(timeout=.2)
            # Only this exact owned synthetic command is terminated. The
            # observer receives no signal; pidfd readiness must end it.
            started = time.monotonic()
            p.terminate()
            stdout, stderr = p.communicate(timeout=3)
            self.assertEqual(p.returncode, -signal.SIGTERM)
            self.assertEqual(stdout, "ready\n")
            self.assertLess(time.monotonic() - started, 1)
            events = records(stderr)
            self.assertIn("target-exited", [e["event"] for e in events])
            observer = next(e["observer_pid"] for e in events
                            if e["event"] == "observer-start")
            status = Path(f"/proc/{observer}/stat")
            deadline = time.monotonic() + .5
            while status.exists():
                try:
                    state = status.read_text().split(") ", 1)[1][0]
                except FileNotFoundError:
                    break
                if state == "Z":
                    break
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.005)
        finally:
            if p.poll() is None:
                p.terminate()
                p.communicate(timeout=3)

    def test_sampling_cap_does_not_end_or_fail_the_command(self):
        p = self.launch("import time; time.sleep(.2); print('finished')",
                        options=("--interval-seconds", ".01", "--max-samples", "3"))
        stdout, stderr = p.communicate(timeout=5)
        events = records(stderr)
        self.assertEqual((p.returncode, stdout), (0, "finished\n"))
        self.assertEqual(len([e for e in events if e["event"] == "sample"]), 3)
        self.assertIn("sample-limit", [e["event"] for e in events])

    def test_fast_exit_cannot_watch_a_reused_or_adoptive_parent_pid(self):
        for _ in range(12):
            p = self.launch("pass")
            stdout, stderr = p.communicate(timeout=3)
            self.assertEqual((p.returncode, stdout), (0, ""))
            self.assertTrue(all(e.get("target_pid", p.pid) == p.pid
                                for e in records(stderr)))
            self.assertIn("target-exited", [e["event"] for e in records(stderr)])

    def test_missing_command_status(self):
        q = subprocess.run([sys.executable, str(SCRIPT), "--", "/no/such/command"],
                           capture_output=True, text=True, timeout=3)
        self.assertEqual(q.returncode, 127)
        self.assertIn("/no/such/command", target_stderr(q.stderr))

    def test_saturated_stderr_does_not_hold_stdout_or_sampler_lifetime(self):
        read_fd, write_fd = os.pipe()
        original_flags = fcntl.fcntl(write_fd, fcntl.F_GETFL)
        fcntl.fcntl(write_fd, fcntl.F_SETFL, original_flags | os.O_NONBLOCK)
        try:
            while True:
                os.write(write_fd, b"x" * 4096)
        except BlockingIOError:
            pass
        fcntl.fcntl(write_fd, fcntl.F_SETFL, original_flags)
        with tempfile.TemporaryDirectory() as unique:
            token = str(Path(unique) / "owned-observer-token")
            command = ("import fcntl,os,time; time.sleep(.08); "
                       "assert not fcntl.fcntl(2,fcntl.F_GETFL)&os.O_NONBLOCK; "
                       "print('done',flush=True)")
            p = subprocess.Popen(
                [sys.executable, str(SCRIPT), "--interval-seconds", ".01",
                 "--max-samples", "1", "--", sys.executable, "-c", command, token],
                stdout=subprocess.PIPE, stderr=write_fd, text=True)
            try:
                p.wait(timeout=3)
                self.assertEqual(p.returncode, 0)
                # stdout must reach EOF even though stderr is never drained.
                stdout, _ = p.communicate(timeout=.25)
                self.assertEqual(stdout, "done\n")
                self.assertEqual(fcntl.fcntl(write_fd, fcntl.F_GETFL), original_flags)
                deadline = time.monotonic() + .5
                while True:
                    active = []
                    for proc in Path("/proc").iterdir():
                        if not proc.name.isdigit():
                            continue
                        try:
                            argv = (proc / "cmdline").read_bytes()
                            if token.encode() in argv and str(SCRIPT).encode() in argv:
                                state = (proc / "stat").read_text().split(") ", 1)[1][0]
                                if state != "Z":
                                    active.append(int(proc.name))
                        except (OSError, IndexError):
                            pass
                    if not active or time.monotonic() >= deadline:
                        break
                    time.sleep(.01)
                self.assertEqual(active, [])
            finally:
                # Release only this test's saturated pipe, including when
                # exercising the known-bad revision; never signal a sampler.
                os.set_blocking(read_fd, False)
                try:
                    while os.read(read_fd, 65536):
                        pass
                except BlockingIOError:
                    pass
                os.close(write_fd)
                p.communicate(timeout=3)
                os.close(read_fd)

    def test_observer_unavailable_does_not_replace_command_failure(self):
        bootstrap = ("import os,runpy,sys; "
                     "script=sys.argv[1]; sys.argv=sys.argv[1:]; "
                     "exec('def unavailable(*args):\\n raise PermissionError(\"injected pidfd failure\")'); "
                     "os.pidfd_open=unavailable; runpy.run_path(script,run_name='__main__')")
        q = subprocess.run([sys.executable, "-c", bootstrap, str(SCRIPT), "--",
                            sys.executable, "-c", "import sys; print('original'); sys.exit(19)"],
                           capture_output=True, text=True, timeout=3)
        self.assertEqual((q.returncode, q.stdout), (19, "original\n"))
        self.assertIn("observer-unavailable", [e["event"] for e in records(q.stderr)])

    def test_oversized_sample_is_bounded(self):
        spec = importlib.util.spec_from_file_location("observer", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        event = {"event": "sample", "processes_omitted": 0,
                 "processes": [{"name": "x" * 1000} for _ in range(32)]}
        with patch.object(module, "_OBSERVER_PROCESS", True), \
                patch.object(module, "_OUTPUT_FD", 123), \
                patch.object(module.os, "write") as write:
            module.emit(event)
        payload = write.call_args.args[1]
        self.assertLessEqual(len(payload), 4096)
        parsed = json.loads(payload.decode()[len(PREFIX):])
        self.assertGreater(parsed["processes_omitted"], 0)


if __name__ == "__main__":
    unittest.main()
