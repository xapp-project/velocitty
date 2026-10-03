"""Remembering the windows and tabs between runs."""
import json
import os

from gi.repository import GLib

STATE_FILE = os.path.join(GLib.get_user_state_dir(), "velocitty", "session.json")
VERSION = 1


class Manager:
    """Remembers the windows as they change, writes them out a moment later, and keeps what
    is needed to bring them back: the windows that are open, and those that were closed while
    others stayed open if something in them was worth keeping (closing their tabs is how to
    forget one)."""

    MAX_KEPT = 20
    WRITE_DELAY = 2   # seconds

    def __init__(self, settings):
        self.settings = settings
        self.pending = None    # what to write: the windows as they were last seen
        self.kept = []         # the windows closed during this run that were worth keeping
        self.frozen = False    # once the app is on its way out, closing windows must not empty it
        self.source = 0        # the timer that writes

    def load(self):
        """The windows of the last session: [{"tabs": [...], "selected": n, "active": bool}, ...]"""
        try:
            with open(STATE_FILE) as handle:
                data = json.load(handle)
            if data.get("version") == VERSION:
                windows = data.get("windows", [])
                return [w for w in windows if isinstance(w, dict) and w.get("tabs")]
        except (OSError, ValueError, AttributeError):
            pass
        return []

    def save(self, windows):
        text = json.dumps({"version": VERSION, "windows": windows}, indent=1)
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            temporary = STATE_FILE + ".tmp"
            with open(temporary, "w") as handle:
                os.chmod(temporary, 0o600)
                handle.write(text)
            os.replace(temporary, STATE_FILE)
        except OSError:
            pass

    def note(self, windows):
        """The windows are now like this: write them out in a moment. A window without tabs is left out."""
        if self.frozen:
            return
        snapshots = [snapshot for snapshot in (window.snapshot() for window in windows) if snapshot["tabs"]]
        if snapshots:
            self.pending = self.kept + snapshots
        if self.pending is not None and not self.source:
            self.source = GLib.timeout_add_seconds(self.WRITE_DELAY, self.write_pending)

    def keep(self, window):
        """A window is going away while others stay: keep it for next time if it is worth it."""
        if window.is_worthy():
            snapshot = window.snapshot()
            snapshot["active"] = False   # the focus is where it is now
            self.kept = (self.kept + [snapshot])[-self.MAX_KEPT:]

    def clear_kept(self):
        """The kept windows are open windows again."""
        self.kept = []

    def freeze(self):
        """What was noted last is the session to keep, whatever happens to the windows now."""
        self.frozen = True

    def flush(self):
        """Write now, if a write was waiting."""
        if self.source:
            GLib.source_remove(self.source)
        self.write_pending()

    def write_pending(self):
        self.source = 0
        if self.pending is not None and self.settings.get_boolean("restore-session"):
            self.save(self.pending)
        return GLib.SOURCE_REMOVE
