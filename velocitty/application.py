import os
import traceback

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from setproctitle import setproctitle
from xapp.util import l10n

from velocitty import APP_ID, GETTEXT_DOMAIN, SETTINGS_SCHEMA
from velocitty.custom_tab import CustomTabs
from velocitty.config import VERSION
from velocitty.palette import build_css, load_palette
from velocitty.preferences import Preferences
from velocitty.shortcuts import ShortcutsWindow
from velocitty.window import TermWindow

_ = l10n(GETTEXT_DOMAIN)

COLOR_SCHEMES = {"system": Adw.ColorScheme.DEFAULT, "light": Adw.ColorScheme.FORCE_LIGHT,
                 "dark": Adw.ColorScheme.FORCE_DARK}


class TermApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.settings = Gio.Settings.new(SETTINGS_SCHEMA)
        self.palette = None
        self.preferences = None
        self.shortcuts = None
        self.custom_tabs = CustomTabs()
        self.launched = False
        self.provider = Gtk.CssProvider()
        self.add_main_option("working-directory", ord("d"), GLib.OptionFlags.NONE,
                             GLib.OptionArg.STRING, "Use DIR for the new terminal", "DIR")
        self.add_main_option("tab", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Always open a new tab", None)
        self.add_main_option("new-window", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Open a new window", None)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: self.apply_scheme())
        self.settings.connect("changed", self.on_settings_changed)
        self.load_settings()

        for name, handler in {
            "new-window": lambda *_: self.new_window(),
            "preferences": lambda *_: self.show_preferences(),
            "about": lambda *_: self.show_about(),
            "quit": lambda *_: self.quit_app(),
        }.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

        for action, accels in {
            "app.new-window": ["<Ctrl><Shift>n"],
            "app.quit": ["<Ctrl><Shift>q"],
            "app.preferences": ["<Ctrl>comma"],
            "win.new-tab": ["<Ctrl><Shift>t"],
            "win.close-tab": ["<Ctrl><Shift>w"],
            "win.custom-tabs": ["F3"],
            "win.overview": ["F4"],
            "win.zoom-in": ["<Ctrl>plus", "<Ctrl>equal", "<Ctrl>KP_Add"],
            "win.zoom-out": ["<Ctrl>minus", "<Ctrl>KP_Subtract"],
            "win.zoom-reset": ["<Ctrl>0", "<Ctrl>KP_0"],
            "win.fullscreen": ["F11"],
            "win.customize-tab": ["F2"],
            "win.main-menu": ["F10"],
            "win.last-tab": ["<Ctrl>End"],
            "win.shortcuts": ["<Ctrl><Shift>question"],
        }.items():
            self.set_accels_for_action(action, accels)

    # -- settings ----------------------------------------------------------

    def load_settings(self):
        self.palette = load_palette(self.settings.get_string("palette"))
        Adw.StyleManager.get_default().set_color_scheme(
            COLOR_SCHEMES.get(self.settings.get_string("color-scheme"), Adw.ColorScheme.DEFAULT))
        self.apply_scheme()

    def on_settings_changed(self, settings, key):
        if key in ("palette", "color-scheme"):
            self.load_settings()
        for window in self.get_windows():
            if isinstance(window, TermWindow):
                window.apply_settings()

    def scheme(self):
        return self.palette["Dark" if Adw.StyleManager.get_default().get_dark() else "Light"]

    def apply_scheme(self):
        self.provider.load_from_string(build_css(self.scheme()))
        for window in self.get_windows():
            if isinstance(window, TermWindow):
                window.apply_scheme()

    # -- windows and launching ---------------------------------------------

    def new_window(self, command=None, cwd=None):
        window = TermWindow(self)
        window.new_tab(command, cwd)
        window.present()
        return window

    def empty_window(self):
        """A window with no tab yet, for one dragged out of another."""
        return TermWindow(self)

    def open_windows(self):
        """The terminal windows that are not closing, the one used last first."""
        return [w for w in self.get_windows() if isinstance(w, TermWindow) and not w.closing]

    def front_window(self):
        windows = self.open_windows()
        return windows[0] if windows else None

    def open_directory(self, front, cwd):
        """Show a tab in this directory: a tab nobody has touched that is already there, else a new one."""
        windows = [front] + [w for w in self.open_windows() if w is not front]
        for window in windows:
            for tab in window.tabs():
                if tab.is_clean_at(cwd):
                    window.tab_view.set_selected_page(window.tab_view.get_page(tab))
                    tab.terminal.grab_focus()
                    window.present()
                    return
        front.new_tab(None, cwd, at_end=True)
        front.present()

    # -- custom tabs -------------------------------------------------------

    def open_startup_tabs(self):
        """At the first launch, open the startup tabs in a window of their own, and return it."""
        first, self.launched = not self.launched, True
        if not first:
            return None
        entries = [entry for entry in self.custom_tabs.entries
                   if entry.get("startup") and not self.is_open(entry)]
        if not entries:
            return None
        # The window is shown once it has its tabs: it takes its size from the terminal
        window = TermWindow(self)
        for entry in entries:
            window.reopen(entry)
        window.tab_view.set_selected_page(window.tab_view.get_nth_page(0))
        window.present()
        return window

    def tab_for(self, entry):
        return next((tab for window in self.open_windows() for tab in window.tabs() if tab.entry is entry), None)

    def is_open(self, entry):
        return self.tab_for(entry) is not None

    def quit_app(self):
        for window in self.open_windows():
            window.record_tabs()
        self.quit()

    def do_command_line(self, cmdline):
        options = cmdline.get_options_dict().end().unpack()
        args = cmdline.get_arguments()[1:]
        cwd = cmdline.get_cwd()
        if options.get("working-directory"):
            # Relative to where the command was typed, not to where the running app is
            cwd = os.path.join(cwd or "", os.path.expanduser(options["working-directory"]))

        # The first thing a launch does is open the startup tabs. After that, the
        # directory it was started in gets a tab, in the window that was in front.
        try:
            startup_window = self.open_startup_tabs()
        except Exception:
            traceback.print_exc()   # a terminal opens whatever is wrong with the saved tabs
            startup_window = None
        front = startup_window or self.front_window()
        if "new-window" in options or front is None:
            self.new_window(args or None, cwd)
        elif args or "tab" in options:
            front.new_tab(args or None, cwd, at_end=True)
            front.present()
        elif startup_window is not None and "working-directory" not in options and (
                not cwd or os.path.realpath(cwd) == os.path.realpath(GLib.get_home_dir())):
            pass   # started from the home directory, where launchers start: just the startup tabs
        else:
            self.open_directory(front, cwd)
        return 0

    def show_preferences(self):
        if self.preferences is None:
            self.preferences = Preferences(self.settings, self.get_active_window())
            self.preferences.connect("close-request", self.on_preferences_closed)
        self.preferences.present()

    def show_shortcuts(self):
        if self.shortcuts is None:
            self.shortcuts = ShortcutsWindow(self.get_active_window())
            self.shortcuts.connect("close-request", self.on_shortcuts_closed)
        self.shortcuts.present()

    def on_shortcuts_closed(self, window):
        self.shortcuts = None
        return False

    def on_preferences_closed(self, window):
        self.preferences = None
        return False

    def show_about(self):
        about = Adw.AboutDialog(application_name="Velocitty", application_icon="velocitty",
                                version=VERSION,
                                website="https://github.com/xapp-project/velocitty")
        about.present(self.get_active_window())


def main(argv):
    setproctitle("velocitty")
    GLib.set_prgname(APP_ID)
    GLib.set_application_name(_("Terminal"))
    return TermApp().run(argv)
