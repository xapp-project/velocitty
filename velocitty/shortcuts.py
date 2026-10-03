import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gtk
from xapp.util import l10n

from velocitty import GETTEXT_DOMAIN

_ = l10n(GETTEXT_DOMAIN)

SECTIONS = [
    (_("General"), [
        (_("New Tab"), "<Ctrl><Shift>t"),
        (_("New Window"), "<Ctrl><Shift>n"),
        (_("Close Tab"), "<Ctrl><Shift>w"),
        (_("Customize Tab"), "F2"),
        (_("View All Tabs"), "<Ctrl><Shift>o"),
        (_("Main Menu"), "F10"),
        (_("Full Screen"), "F11"),
        (_("Preferences"), "<Ctrl>comma"),
        (_("Keyboard Shortcuts"), "<Ctrl><Shift>question"),
        (_("Quit"), "<Ctrl><Shift>q"),
    ]),
    (_("Terminal"), [
        (_("Copy"), "<Ctrl><Shift>c"),
        (_("Paste"), "<Ctrl><Shift>v"),
        (_("Select All"), "<Ctrl><Shift>a"),
        (_("Find"), "<Ctrl><Shift>f"),
        (_("Context Menu"), "<Shift>F10"),
        (_("Zoom In"), "<Ctrl>plus"),
        (_("Zoom Out"), "<Ctrl>minus"),
        (_("Reset Zoom"), "<Ctrl>0"),
    ]),
    (_("Tabs"), [
        (_("Previous Tab"), "<Ctrl>Page_Up"),
        (_("Next Tab"), "<Ctrl>Page_Down"),
        (_("Last Active Tab"), "<Ctrl>End"),
        (_("Move Tab Left"), "<Ctrl><Shift>Page_Up"),
        (_("Move Tab Right"), "<Ctrl><Shift>Page_Down"),
        (_("Switch to Tab 1"), "<Alt>1"),
        (_("Switch to Tab 2"), "<Alt>2"),
        (_("Switch to Tab 3"), "<Alt>3"),
        (_("Switch to Tab 4"), "<Alt>4"),
        (_("Switch to Tab 5"), "<Alt>5"),
        (_("Switch to Tab 6"), "<Alt>6"),
        (_("Switch to Tab 7"), "<Alt>7"),
        (_("Switch to Tab 8"), "<Alt>8"),
        (_("Switch to Tab 9"), "<Alt>9"),
        (_("Switch to Tab 10"), "<Alt>0"),
    ]),
]


class ShortcutsWindow(Adw.Window):
    """A window that lists the shortcuts."""

    def __init__(self, parent):
        super().__init__(title=_("Keyboard Shortcuts"), modal=False, transient_for=parent,
                         default_width=480, default_height=640)
        page = Adw.PreferencesPage()
        for title, items in SECTIONS:
            group = Adw.PreferencesGroup(title=title)
            for name, accelerator in items:
                row = Adw.ActionRow(title=name)
                row.add_suffix(Adw.ShortcutLabel(accelerator=accelerator, valign=Gtk.Align.CENTER))
                group.add(row)
            page.add(group)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(Adw.HeaderBar())
        self.set_content(view)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)

    def on_key(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False
