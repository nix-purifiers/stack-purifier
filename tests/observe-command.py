#!/usr/bin/env python3
"""Observe a CI command without wrapping its exit status or controlling its jobs."""
import argparse
import json
import os
from pathlib import Path
import select
import sys
import time

PREFIX = "[stack-ci-observe] "
MAX_PROCESSES = 32
MAX_LINE_BYTES = 4096
TOOLS = ("Setup", "haddock", "ghc", "nix")


def read(path, limit=512):
    try:
        return path.read_text()[:limit].strip()
    except (OSError, UnicodeError):
        return None


def emit(event):
    line = PREFIX + json.dumps(event, separators=(",", ":")) + "\n"
    while len(line.encode()) > MAX_LINE_BYTES and event.get("processes"):
        event["processes"].pop()
        event["processes_omitted"] += 1
        line = PREFIX + json.dumps(event, separators=(",", ":")) + "\n"
    if len(line.encode()) > MAX_LINE_BYTES:
        line = PREFIX + json.dumps({"event": "sample-too-large"}) + "\n"
    try:
        os.write(2, line.encode())
    except OSError:
        pass


def processes(target):
    result = []
    try:
        entries = Path("/proc").iterdir()
        for entry in entries:
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            text = read(entry / "stat", 8192)
            if not text:
                continue
            left, right = text.find("("), text.rfind(")")
            name = text[left + 1:right]
            if int(entry.name) != target and not name.startswith(TOOLS):
                continue
            fields = text[right + 2:].split()
            try:
                result.append({
                    "pid": int(entry.name), "ppid": int(fields[1]),
                    "name": name[:64], "state": fields[0],
                    "cpu_ticks": int(fields[11]) + int(fields[12]),
                    "rss_pages": int(fields[21]), "threads": int(fields[17]),
                    "start_ticks": int(fields[19]),
                    "wait_channel": read(entry / "wchan", 64),
                })
            except (ValueError, IndexError):
                continue
    except OSError:
        pass
    result.sort(key=lambda item: (item["pid"] != target, -item["rss_pages"], item["pid"]))
    return result[:MAX_PROCESSES], max(0, len(result) - MAX_PROCESSES)


def snapshot(target, number):
    rows, omitted = processes(target)
    memory = {}
    for line in (read(Path("/proc/meminfo"), 16384) or "").splitlines():
        key, _, value = line.partition(":")
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            memory[key] = value.strip()
    cgroup = Path("/sys/fs/cgroup")
    for line in (read(Path("/proc/self/cgroup"), 4096) or "").splitlines():
        if line.startswith("0::"):
            candidate = cgroup / line[3:].lstrip("/")
            if candidate.is_dir():
                cgroup = candidate
    return {
        "event": "sample", "number": number, "unix_time": time.time(),
        "target_pid": target, "processes": rows, "processes_omitted": omitted,
        "memory": memory,
        "pressure": {name: read(Path("/proc/pressure") / name)
                     for name in ("memory", "cpu", "io")},
        "cgroup": {name: read(cgroup / name)
                   for name in ("memory.current", "memory.max", "memory.peak",
                                "memory.events", "memory.pressure", "cpu.stat")},
    }


def observe(pidfd, target, interval, limit):
    emit({"event": "observer-start", "observer_pid": os.getpid(),
          "target_pid": target, "interval_seconds": interval, "max_samples": limit,
          "cpu_count": os.cpu_count(), "page_size": os.sysconf("SC_PAGE_SIZE"),
          "clock_ticks": os.sysconf("SC_CLK_TCK"),
          "NIX_BUILD_CORES": os.environ.get("NIX_BUILD_CORES")})
    for number in range(limit):
        if select.select([pidfd], [], [], 0)[0]:
            emit({"event": "target-exited", "target_pid": target})
            return
        emit(snapshot(target, number))
        if select.select([pidfd], [], [], interval)[0]:
            emit({"event": "target-exited", "target_pid": target})
            return
    emit({"event": "sample-limit", "target_pid": target, "samples": limit})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval-seconds", type=float, default=60)
    parser.add_argument("--max-samples", type=int, default=120)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")
    if not 0 < args.interval_seconds <= 60 or not 1 <= args.max_samples <= 120:
        parser.error("interval must be in (0, 60] and sample count in [1, 120]")

    # Open before forking: even a command that exits immediately cannot race
    # the observer into watching a reused PID or its new adoptive parent.
    pidfd = None
    try:
        target = os.getpid()
        pidfd = os.pidfd_open(target)
        child = os.fork()
    except (AttributeError, OSError) as error:
        if pidfd is not None:
            os.close(pidfd)
        emit({"event": "observer-unavailable", "error": str(error)[:256]})
    else:
        if child == 0:
            try:
                # Reap this intermediate child before exec, so the command
                # does not inherit an observer child that it might reap.
                if os.fork() != 0:
                    os._exit(0)
                try:
                    os.close(0)
                except OSError:
                    pass
                observe(pidfd, target, args.interval_seconds, args.max_samples)
            except BaseException as error:
                emit({"event": "observer-error", "error": str(error)[:256]})
            finally:
                os.close(pidfd)
                os._exit(0)
        try:
            os.waitpid(child, 0)
        except ChildProcessError:
            pass  # An inherited SIGCHLD disposition may already reap it.
        os.close(pidfd)
    # No wait/trap/signal relay around the command: it retains this PID,
    # stdin/stdout/stderr, environment, working directory, and normal status.
    try:
        os.execvpe(command[0], command, os.environ)
    except OSError as error:
        print(f"{command[0]}: {error.strerror}", file=sys.stderr)
        return 127 if error.errno == 2 else 126


if __name__ == "__main__":
    sys.exit(main())
