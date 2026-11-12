"""Start / stop / status logic for a Service."""
from __future__ import annotations

import os
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import psutil

from config import Service

CREATE_NEW_CONSOLE = 0x00000010
CREATE_NO_WINDOW = 0x08000000

STOPPED, STARTING, RUNNING = "stopped", "starting", "running"


# ---------- status ----------

def port_open(port: int, timeout: float = 0.25) -> bool:
    if not port:
        return False
    for host in ("127.0.0.1", "::1"):
        fam = socket.AF_INET6 if ":" in host else socket.AF_INET
        try:
            with socket.socket(fam, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                if s.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def snapshot_processes() -> list[tuple[int, str, str]]:
    me = os.getpid()
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.info["pid"] == me:
                continue
            out.append((p.info["pid"], (p.info["name"] or "").lower(),
                        " ".join(p.info["cmdline"] or []).lower()))
        except (psutil.Error, OSError):
            continue
    return out


def matching_pids(svc: Service, procs: list[tuple[int, str, str]]) -> list[int]:
    m = svc.process_match.strip().lower()
    if not m:
        return []
    return [pid for pid, name, cmd in procs if m == name or m in name or m in cmd]


def status(svc: Service, procs: list[tuple[int, str, str]]) -> str:
    if svc.port and port_open(svc.port):
        return RUNNING
    if matching_pids(svc, procs):
        return STARTING if svc.port else RUNNING
    return STOPPED


# ---------- start / stop ----------

def _write_script(svc: Service, kind: str, text: str) -> list[str]:
    d = Path(tempfile.gettempdir()) / "servicepanel"
    d.mkdir(exist_ok=True)
    safe = re.sub(r"[^\w\-]+", "_", svc.name)
    if svc.shell == "powershell":
        f = d / f"{safe}_{kind}.ps1"
        f.write_text(text, encoding="utf-8-sig")
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(f)]
    f = d / f"{safe}_{kind}.cmd"
    f.write_text("@echo off\r\n" + text.replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n",
                 encoding="oem", errors="replace")
    return ["cmd", "/c", str(f)]


def _cwd(svc: Service) -> str | None:
    return svc.cwd if svc.cwd and os.path.isdir(svc.cwd) else None


def start(svc: Service) -> str:
    if not svc.start_cmd.strip():
        return "No start command set."
    argv = _write_script(svc, "start", svc.start_cmd)
    flags = CREATE_NEW_CONSOLE if svc.show_console else CREATE_NO_WINDOW
    subprocess.Popen(argv, cwd=_cwd(svc), creationflags=flags, close_fds=True)
    return f"Started '{svc.name}'."


def _pids_on_port(port: int) -> set[int]:
    pids = set()
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN and c.pid:
                pids.add(c.pid)
    except (psutil.Error, OSError):
        pass
    return pids


def kill_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except psutil.Error:
                pass
        parent.kill()
    except psutil.Error:
        pass


def stop(svc: Service) -> str:
    log = []
    if svc.stop_cmd.strip():
        argv = _write_script(svc, "stop", svc.stop_cmd)
        try:
            r = subprocess.run(argv, cwd=_cwd(svc), capture_output=True, timeout=180,
                               creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
            out = (r.stdout + r.stderr).decode("oem", errors="replace").strip()
            log.append(f"Stop command finished (exit {r.returncode}).")
            if out:
                log.append(out[-2000:])
        except subprocess.TimeoutExpired:
            log.append("Stop command timed out after 3 minutes.")
        time.sleep(1.5)

    if svc.kill_leftovers:
        procs = snapshot_processes()
        if status(svc, procs) != STOPPED:
            pids = _pids_on_port(svc.port) | set(matching_pids(svc, procs))
            for pid in pids:
                try:
                    name = psutil.Process(pid).name()
                except psutil.Error:
                    name = "?"
                kill_tree(pid)
                log.append(f"Killed leftover {name} (pid {pid}).")
            time.sleep(0.8)
            if status(svc, snapshot_processes()) != STOPPED:
                log.append("Still running; it may be running as administrator "
                           "(then run this panel as administrator too).")
    if not log:
        log.append("Nothing to stop.")
    return "\n".join(log)


def listening_ports() -> list[dict]:
    """All TCP listeners: port, pid, process name, exe, command line, cwd."""
    rows, seen = [], set()
    try:
        conns = psutil.net_connections(kind="tcp")
    except (psutil.Error, OSError):
        return rows
    for c in conns:
        if c.status != psutil.CONN_LISTEN or not c.laddr or not c.pid:
            continue
        key = (c.laddr.port, c.pid)
        if key in seen:
            continue
        seen.add(key)
        name = exe = cmd = cwd = ""
        try:
            p = psutil.Process(c.pid)
            name = p.name()
            try:
                exe = p.exe()
                cmd = subprocess.list2cmdline(p.cmdline())
                cwd = p.cwd()
            except (psutil.Error, OSError):
                pass
        except psutil.Error:
            continue
        rows.append({"port": c.laddr.port, "pid": c.pid, "name": name, "exe": exe,
                     "cmd": cmd, "cwd": cwd, "local": c.laddr.ip in ("127.0.0.1", "::1")})
    return sorted(rows, key=lambda r: r["port"])
