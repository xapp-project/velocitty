import json
import os

from gi.repository import GLib, GObject

FILE = os.path.join(GLib.get_user_state_dir(), "velocitty", "tabs.json")


class CustomTabs(GObject.Object):

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self):
        super().__init__()
        self.entries = self.load()

    def load(self):
        try:
            with open(FILE) as handle:
                return [entry for entry in json.load(handle) if entry.get("cwd")]
        except (OSError, ValueError, AttributeError, TypeError):
            return []

    def save(self):
        try:
            os.makedirs(os.path.dirname(FILE), exist_ok=True)
            temporary = FILE + ".tmp"
            with open(temporary, "w") as handle:
                os.chmod(temporary, 0o600)
                json.dump(self.entries, handle, indent=1)
            os.replace(temporary, FILE)
        except OSError:
            pass

    def changed(self):
        self.save()
        self.emit("changed")

    def index(self, entry):
        return next((i for i, e in enumerate(self.entries) if e is entry), -1)

    def add_first(self, entry):
        self.entries.insert(0, entry)

    def save_open(self, entry, snapshot):
        if self.index(entry) < 0:
            entry = dict(snapshot)
            self.add_first(entry)
        else:
            entry.clear()
            entry.update(snapshot)
        self.changed()
        return entry

    def close(self, entry, snapshot):
        if self.index(entry) < 0:
            entry = {}
            self.add_first(entry)
        entry.clear()
        entry.update(snapshot)
        self.changed()

    def move(self, entry, index):
        self.entries.remove(entry)
        self.entries.insert(index, entry)
        self.changed()

    def remove(self, entry):
        index = self.index(entry)
        if index >= 0:
            del self.entries[index]
            self.changed()

    def set_startup(self, entry, startup):
        entry["startup"] = startup
        self.save()
