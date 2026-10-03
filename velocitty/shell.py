"""Finding out what a terminal is running."""

import os
import socket

from gi.repository import GLib

SHELLS = {"bash", "zsh", "fish", "sh", "dash", "ksh", "tcsh"}
# Programs that give a session on another machine
REMOTE_CLIENTS = {"ssh", "mosh", "mosh-client", "sftp", "scp", "ftp", "lftp", "telnet"}


def _read_proc(pid, name):
    try:
        with open("/proc/%d/%s" % (pid, name), "rb") as f:
            return f.read()
    except OSError:
        return b""


def _effective_uid(pid):
    for line in _read_proc(pid, "status").decode(errors="replace").splitlines():
        if line.startswith("Uid:"):
            return int(line.split()[2])
    return None


def classify(pty, shell_pid):
    """Return (kind, command_line) for the foreground process of a pty.

    kind is "idle" (at the prompt), "active" (a program is running), "remote" (ssh and the
    like) or "admin".
    """
    if pty is None:
        return "idle", ""
    try:
        pgid = os.tcgetpgrp(pty.get_fd())
    except OSError:
        return "idle", ""

    comm = os.path.basename(_read_proc(pgid, "comm").decode(errors="replace").strip())
    command_line = _read_proc(pgid, "cmdline").replace(b"\0", b" ").decode(errors="replace").strip()

    if _effective_uid(pgid) == 0 and os.getuid() != 0:
        return "admin", command_line
    if comm in REMOTE_CLIENTS:
        return "remote", command_line
    if pgid == shell_pid or comm in SHELLS:
        return "idle", ""
    return "active", command_line


def strip_local_host(title):
    """Remove a leading "user@host: " when it is about this machine."""
    prefix = "%s@%s: " % (GLib.get_user_name(), socket.gethostname().split(".")[0])
    return title[len(prefix):] if title.startswith(prefix) else title


def installed_shells():
    """The shells listed in /etc/shells that are installed, as (path, name),
    one per name (it lists both /bin/bash and /usr/bin/bash on some systems)."""
    shells, seen = [], set()
    try:
        with open("/etc/shells") as f:
            lines = f.read().splitlines()
    except OSError:
        return shells
    for line in lines:
        path = line.strip()
        if not path or path.startswith("#") or not os.access(path, os.X_OK):
            continue
        name = os.path.basename(path)
        if name in seen or name in ("sh", "rbash", "nologin", "false", "git-shell"):
            continue
        seen.add(name)
        shells.append((path, name))
    return shells
