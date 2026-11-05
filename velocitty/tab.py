import os
import shlex
import signal
import socket
import tempfile

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte
from xapp.util import l10n

from velocitty import GETTEXT_DOMAIN
from velocitty.palette import rgba, terminal_colors
from velocitty.searchbar import SearchBar
from velocitty.shell import classify, shorten_path, strip_local_host, untrim_path

_ = l10n(GETTEXT_DOMAIN)

MAX_LAST_OUTPUT_ROWS = 10000
FIRST_PROMPT_WAIT = 2000   # milliseconds a shell that gives no prompt signal is waited for
PROMPT_SETTLE = 60   # milliseconds after the signal for the prompt to be drawn
# Links are found in the text, or given by the program (OSC 8). Only these kinds are opened.
LINK_PATTERN = r"(?:(?:https?|ftp)://|www\.|mailto:)[^\s<>\"'`{}|\\^\[\]]+[^\s<>\"'`{}|\\^\[\].,;:!?)]"
LINK_SCHEMES = ("http", "https", "ftp", "mailto")
PCRE2_CASELESS = 0x00000008
PCRE2_MULTILINE = 0x00000400
ORPHAN_POLLS = 120   # half a second each: a minute without a window
SETTLE_USEC = 700000
ZOOM_LEVELS = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0]
ZOOM_DEFAULT = ZOOM_LEVELS.index(1.0)
TERMINAL_MARGIN = 6
CURSOR_SHAPES = {"block": Vte.CursorShape.BLOCK, "ibeam": Vte.CursorShape.IBEAM,
                 "underline": Vte.CursorShape.UNDERLINE}
CURSOR_BLINK = {"system": Vte.CursorBlinkMode.SYSTEM, "on": Vte.CursorBlinkMode.ON,
                "off": Vte.CursorBlinkMode.OFF}


class TermTab(Gtk.Overlay):
    """A terminal and what we know about the session running in it."""

    def __init__(self, window, settings, command=None, cwd=None, restore=None):
        super().__init__()
        self.window = window
        self.settings = settings
        # The command to run at start is for new tabs only, not for restored ones nor for
        # tabs asked to run a command
        self.runs_startup = not command and restore is None
        self.started_with_command = bool(command)   # not a shell: the command is the whole tab
        self.used = False   # Enter was pressed in this tab
        self.orphan_polls = 0
        restore = restore or {}
        self.startup = bool(restore.get("startup"))   # opens when the terminal launches
        self.shell_exited = False   # the shell is gone: the tab closes without asking
        self.entry = None   # the saved copy of this tab, if it has one
        self.custom_title = restore.get("title") or None   # a title the user chose
        self.custom_color = restore.get("color") or None   # a custom color the user chose, by name
        self.run_command = restore.get("run") or None      # run when the tab is opened
        self.last_cwd = cwd
        self.auto_title = _("Terminal")
        self.kind = "idle"
        self.command_line = ""
        self.title = self.window_title = _("Terminal")   # in the tab bar, and in the headerbar
        self.shell_pid = None
        self.last_output = None
        self.prompt_row = None
        self.prompt_text = None

        self.terminal = Vte.Terminal()
        self.terminal.set_hexpand(True)
        self.terminal.set_vexpand(True)
        for side in ("start", "end", "top", "bottom"):
            getattr(self.terminal, "set_margin_" + side)(TERMINAL_MARGIN)
        self.terminal.set_size(settings.get_int("default-columns"), settings.get_int("default-rows"))
        self.scroller = scrolled = Gtk.ScrolledWindow(child=self.terminal, propagate_natural_width=True,
                                      propagate_natural_height=True, css_classes=["term-scroller"])
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content.append(scrolled)
        self.set_child(self.content)
        self.apply_settings()
        self.setup_size_overlay()
        self.search_bar = SearchBar(self.terminal, settings)
        self.content.append(self.search_bar)

        self.terminal.connect("child-exited", self.on_child_exited)
        self.terminal.connect("notify::window-title", lambda *_: self.update_title())
        self.terminal.connect("termprop-changed::vte.shell.precmd", self.on_precmd)

        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll.connect("scroll", self.on_scroll)
        self.terminal.add_controller(scroll)

        # Typing at the prompt takes over from the command that was typed for the user
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key_pressed)
        self.terminal.add_controller(keys)

        # Files and folders dropped on the terminal are typed as their quoted paths. This is on
        # the tab, in the capture phase, so that it comes before any handling by the terminal.
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)

        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self.on_context_menu)
        self.terminal.add_controller(click)
        # Ctrl+click opens a link. This is in the capture phase so that it comes before the
        # terminal starts a selection.
        link_click = Gtk.GestureClick(button=1, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        link_click.connect("pressed", self.on_link_click)
        self.terminal.add_controller(link_click)
        self.terminal.set_allow_hyperlink(True)
        regex = Vte.Regex.new_for_match(LINK_PATTERN, -1, PCRE2_MULTILINE | PCRE2_CASELESS)
        self.terminal.match_set_cursor_name(self.terminal.match_add_regex(regex, 0), "pointer")
        self.link = None   # the link under the pointer when the context menu was opened
        self.setup_actions()

        self.pending_command = self.startup_command()
        env = ["%s=%s" % item for item in os.environ.items() if item[0] not in ("TERM", "PROMPT_DIRTRIM")]
        env += ["TERM=xterm-256color", "COLORTERM=truecolor",
                "PROMPT_DIRTRIM=%d" % self.settings.get_int("prompt-folders")]
        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT, cwd or GLib.get_home_dir(), command or self.shell_argv(), env,
            GLib.SpawnFlags.DEFAULT, None, None, -1, None, self.on_spawned)
        GLib.timeout_add(500, self.poll)

    def do_grab_focus(self):
        # GTK hands focus back to the popover's parent when the context menu closes. An
        # overlay can't take it, and GTK would then focus the first widget in the window.
        return self.terminal.grab_focus()

    def shell_argv(self):
        shell = self.settings.get_string("shell")
        if not shell or not os.access(shell, os.X_OK):
            shell = os.environ.get("SHELL", "/bin/bash")
        if self.pending_command and os.path.basename(shell) == "fish":
            startup, self.pending_command = self.pending_command, ""
            return [shell, "--init-command", startup]
        if self.pending_command and os.path.basename(shell) == "bash":
            startup, self.pending_command = self.pending_command, ""
            handle, path = tempfile.mkstemp(prefix="velocitty-", suffix=".rc", dir=GLib.get_user_runtime_dir())
            with os.fdopen(handle, "w") as rcfile:
                rcfile.write("rm -f %s\n" % shlex.quote(path))
                rcfile.write("[ -f ~/.bashrc ] && . ~/.bashrc\n")
                rcfile.write(startup + "\n")
            return [shell, "--rcfile", path, "-i"]
        return [shell]

    def startup_command(self):
        """What is typed in the shell when it starts: the command of a saved tab, else the startup
        command of the settings. That one doesn't apply to restored tabs, nor to a tab asked to run
        a specific command."""
        if self.run_command:
            return self.run_command
        return self.settings.get_string("startup-command").strip() if self.runs_startup else ""

    # -- settings and colors -----------------------------------------------

    def apply_settings(self):
        s = self.settings
        self.terminal.set_bold_is_bright(s.get_boolean("bold-is-bright"))
        self.terminal.set_scrollback_lines(s.get_int("scrollback-lines") if s.get_boolean("limit-scrollback") else -1)
        self.terminal.set_scroll_on_keystroke(s.get_boolean("scroll-on-keystroke"))
        self.terminal.set_scroll_on_output(s.get_boolean("scroll-on-output"))
        scrollbars = s.get_string("scrollbars")
        self.scroller.set_policy(Gtk.PolicyType.AUTOMATIC,
                                 Gtk.PolicyType.NEVER if scrollbars == "never" else Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_overlay_scrolling(scrollbars != "always")
        self.terminal.set_cursor_shape(CURSOR_SHAPES.get(s.get_string("cursor-shape"), Vte.CursorShape.BLOCK))
        self.terminal.set_cursor_blink_mode(CURSOR_BLINK.get(s.get_string("cursor-blink"), Vte.CursorBlinkMode.SYSTEM))
        self.terminal.set_audible_bell(False)  # there is no beep, see the visual bell
        self.terminal.set_font(Pango.FontDescription.from_string(self.font_name()))
        self.terminal.set_cell_height_scale(s.get_double("line-spacing"))
        self.terminal.set_cell_width_scale(s.get_double("column-spacing"))
        zoom = s.get_double("zoom")
        if zoom != self.terminal.get_font_scale():
            self.terminal.set_font_scale(zoom)

    def font_name(self):
        if not self.settings.get_boolean("use-system-font"):
            return self.settings.get_string("font")
        try:
            return Gio.Settings.new("org.gnome.desktop.interface").get_string("monospace-font-name")
        except GLib.Error:
            return "Monospace 11"

    def apply_colors(self, p):
        self.terminal.set_colors(rgba(p["Foreground"]), rgba(p["Background"]), terminal_colors(p))

    def current_directory(self):
        """The directory for a new tab opened from this one, or None.

        Only a local directory that exists is safe to use: over ssh the shell
        reports a path on the other machine."""
        uri = self.terminal.get_current_directory_uri()
        if not uri:
            return None
        try:
            path, host = GLib.filename_from_uri(uri)
        except GLib.Error:
            return None
        if host not in (None, "", "localhost", socket.gethostname()):
            return None
        return path if os.path.isdir(path) else None

    # -- size indicator ----------------------------------------------------

    def setup_size_overlay(self):
        """Show the columns and rows while the terminal is being resized."""
        self.size_label = Gtk.Label(css_classes=["size-indicator"], margin_end=24, margin_bottom=18)
        self.size_revealer = Gtk.Revealer(child=self.size_label, halign=Gtk.Align.END,
                                          valign=Gtk.Align.END, can_target=False,
                                          transition_type=Gtk.RevealerTransitionType.CROSSFADE)
        self.add_overlay(self.size_revealer)
        self.terminal.connect("bell", self.on_bell)
        self.last_size = None
        self.created = GLib.get_monotonic_time()
        self.size_hide_source = 0
        self.size_check_ticks = 0

    def window_resized(self):
        """The window changed size. The terminal takes its new columns and rows when the next frames
        are laid out, so wait two of them. There is no callback running when nothing is resized."""
        if not self.size_check_ticks:
            self.size_check_ticks = 2
            self.terminal.add_tick_callback(self.check_size)

    def check_size(self, widget, clock):
        self.size_check_ticks -= 1
        if self.size_check_ticks:
            return GLib.SOURCE_CONTINUE
        size = (self.terminal.get_column_count(), self.terminal.get_row_count())
        if size != self.last_size:
            self.last_size = size
            # The window settles to its size right after it appears, only show real resizes
            if GLib.get_monotonic_time() - self.created >= SETTLE_USEC:
                self.show_indicator("%d \u00d7 %d" % size)
        return GLib.SOURCE_REMOVE

    def show_indicator(self, text):
        """Show a short text, the size or the zoom, in the corner for a moment."""
        self.size_label.set_text(text)
        self.size_revealer.set_reveal_child(True)
        if self.size_hide_source:
            GLib.source_remove(self.size_hide_source)
        self.size_hide_source = GLib.timeout_add(1000, self.hide_size)

    def hide_size(self):
        self.size_hide_source = 0
        self.size_revealer.set_reveal_child(False)
        return GLib.SOURCE_REMOVE

    # -- visual bell -------------------------------------------------------

    def on_bell(self, terminal):
        if not self.settings.get_boolean("visual-bell"):
            return
        if self.window.current_tab() is self and self.window.is_active():
            self.window.flash_headerbar()
        else:
            # Not in front: mark the tab, until it is selected again
            self.window.tab_view.get_page(self).set_needs_attention(True)

    # -- zoom --------------------------------------------------------------

    def zoom(self, step):
        """Zoom in (+1), out (-1), or back to normal (0). The level is shared by
        all terminals and remembered."""
        if step == 0:
            index = ZOOM_DEFAULT
        else:
            current = self.settings.get_double("zoom")
            nearest = min(range(len(ZOOM_LEVELS)), key=lambda i: abs(ZOOM_LEVELS[i] - current))
            index = max(0, min(len(ZOOM_LEVELS) - 1, nearest + step))
        self.settings.set_double("zoom", ZOOM_LEVELS[index])
        self.show_indicator("%d%%" % round(ZOOM_LEVELS[index] * 100))

    def on_scroll(self, controller, dx, dy):
        if not controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return False
        self.zoom(-1 if dy > 0 else 1)
        return True

    # -- session detection -------------------------------------------------

    def on_child_exited(self, terminal, status):
        """This is called when the shell exited by itself, so its PID is already dead."""
        self.shell_pid = None
        self.shell_exited = True
        self.window.close_tab(self)

    def hangup(self):
        """This is called when we manually close a tab or a window."""
        pty = self.terminal.get_pty()
        if pty is not None:
            # Kill whatever is running in the shell
            try:
                os.killpg(os.tcgetpgrp(pty.get_fd()), signal.SIGHUP)
            except OSError:
                pass
        if self.shell_pid:
            # Kill the shell itself
            try:
                os.kill(self.shell_pid, signal.SIGHUP)
                handle = os.pidfd_open(self.shell_pid)
                # Give the shell some time to save its history and all..
                GLib.timeout_add(500, self.force_quit, handle)
            except OSError:
                pass   # it is already gone
            self.shell_pid = None

    def force_quit(self, handle):
        """If the shell is still there, kill it for good."""
        try:
            signal.pidfd_send_signal(handle, signal.SIGKILL)
        except OSError:
            pass
        os.close(handle)
        return GLib.SOURCE_REMOVE

    def on_spawned(self, terminal, pid, error):
        self.shell_pid = pid
        # The shell starts once, so its startup files are read once. The command is typed when the
        # first prompt is shown, or after a while for a shell that doesn't tell us
        if self.pending_command and not error:
            GLib.timeout_add(FIRST_PROMPT_WAIT, self.type_pending_command)

    def type_pending_command(self):
        command, self.pending_command = self.pending_command, ""
        if command and self.get_root() is not None:
            self.terminal.feed_child((command + "\n").encode())
        return GLib.SOURCE_REMOVE

    def on_key_pressed(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.used = True   # something was run here, even if it is over by now
        return False

    def is_clean_at(self, path):
        """A tab nobody has touched, sitting in this directory: nothing was customized and
        nothing is running."""
        if (self.started_with_command or self.used or self.custom_title or self.custom_color
                or self.kind != "idle"):
            return False
        directory = self.current_directory() or self.last_cwd
        return bool(directory) and os.path.realpath(directory) == os.path.realpath(path)

    def snapshot(self):
        """What is needed to bring this tab back."""
        directory = self.current_directory()
        if directory:
            self.last_cwd = directory
        return {"cwd": self.last_cwd, "title": self.custom_title, "color": self.custom_color,
                "run": self.run_command, "startup": self.startup}

    def poll(self):
        # A tab being dragged to another window has none for a while: keep going, and only
        # give up when it has been gone for long (it was closed)
        if self.get_root() is None:
            self.orphan_polls += 1
            return GLib.SOURCE_REMOVE if self.orphan_polls > ORPHAN_POLLS else GLib.SOURCE_CONTINUE
        self.orphan_polls = 0
        state = classify(self.terminal.get_pty(), self.shell_pid)
        if state != (self.kind, self.command_line):
            self.kind, self.command_line = state
            self.update_title()
            self.window.refresh()
        return GLib.SOURCE_CONTINUE

    # -- title -------------------------------------------------------------

    def style_class(self):
        """The CSS class that colors this tab: the custom color the user chose, else the color
        of its session (ssh, admin)."""
        if self.custom_color:
            return "custom-" + self.custom_color
        if self.kind in ("remote", "admin") and self.settings.get_boolean("highlight-sessions"):
            return self.kind
        return None

    def is_running(self):
        """A program is running, and it is highlighted: a shade over the color of the tab."""
        return self.kind == "active" and self.settings.get_boolean("highlight-running")

    def update_title(self):
        """The automatic title is in full in the tooltip. The tab and the window show a folder
        shortened to its last folders, as many as the settings say."""
        shown = strip_local_host(self.terminal.get_window_title() or _("Terminal"))
        if self.kind in ("idle", "active"):
            shown = untrim_path(shown, self.shell_pid)
        suffix = " — " + self.command_line if self.kind == "active" and self.command_line else ""
        self.auto_title = shown + suffix
        self.title = self.custom_title or shorten_path(shown, self.settings.get_int("tab-folders")) + suffix
        self.window_title = self.custom_title or shorten_path(shown, self.settings.get_int("window-folders")) + suffix
        self.window.title_changed(self)

    # -- drop and paste ----------------------------------------------------

    def on_drop(self, target, value, x, y):
        """Type the paths of what was dropped, each between single quotes. A single folder
        dropped on an empty prompt gets a cd in front."""
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if not paths:
            return False
        text = " ".join("'%s'" % path.replace("'", "'\\''") for path in paths)
        if len(paths) == 1 and os.path.isdir(paths[0]) and self.prompt_is_empty():
            text = "cd " + text
        self.terminal.paste_text(text)
        self.terminal.grab_focus()
        return True

    def paste(self):
        self.terminal.paste_clipboard()

    # -- prompt and last output --------------------------------------------

    def prompt_is_empty(self):
        """At a local shell prompt with nothing typed after it."""
        if self.kind != "idle" or self.prompt_row is None:
            return False
        column, row = self.terminal.get_cursor_position()
        return row == self.prompt_row and self.rows(row, row) == self.prompt_text

    def on_precmd(self, terminal, *args):
        """The shell is about to prompt: everything since the previous prompt
        is the last command and its output."""
        if self.pending_command:
            # Once the prompt is on screen, so that the terminal doesn't echo the command twice
            GLib.timeout_add(PROMPT_SETTLE, self.type_pending_command)
        column, row = terminal.get_cursor_position()
        if self.prompt_row is not None and row - 1 >= self.prompt_row:
            start = max(self.prompt_row, row - MAX_LAST_OUTPUT_ROWS)
            text = self.rows(start, row - 1)
            # Pressing Enter on an empty prompt is not a command
            if text and not (self.prompt_row == row - 1 and text == self.prompt_text):
                self.last_output = text
                self.actions.lookup_action("copy-last").set_enabled(True)
        self.prompt_text = self.rows(row, row)
        self.prompt_row = row

    def rows(self, start, end):
        text, _length = self.terminal.get_text_range_format(
            Vte.Format.TEXT, start, 0, end, self.terminal.get_column_count())
        return text.rstrip() if text else text

    def copy_last_output(self):
        if not self.last_output:
            return
        # Both Ctrl+V and middle-click paste should get the output
        self.get_clipboard().set(self.last_output)
        self.get_primary_clipboard().set(self.last_output)

    # -- search ------------------------------------------------------------

    def show_search(self):
        self.search_bar.open()

    # -- actions and context menu ------------------------------------------

    def setup_actions(self):
        self.actions = Gio.SimpleActionGroup()
        handlers = {
            "copy": lambda *_: self.terminal.copy_clipboard(),
            "paste": lambda *_: self.paste(),
            "copy-last": lambda *_: self.copy_last_output(),
            "select-all": lambda *_: self.terminal.select_all(),
            "find": lambda *_: self.show_search(),
            "menu": lambda *_: self.popup_menu_at_cursor(),
            "open-link": lambda *_: self.open_link(self.link),
        }
        for name, handler in handlers.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.actions.add_action(action)
        self.actions.lookup_action("copy-last").set_enabled(False)
        self.insert_action_group("term", self.actions)

        # The window can't reach the tab's actions, so the shortcuts live here
        shortcuts = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        for accel, name in (("<Ctrl><Shift>c", "copy"), ("<Ctrl><Shift>v", "paste"),
                            ("<Ctrl><Shift>f", "find"), ("<Ctrl><Shift>a", "select-all"),
                            ("<Shift>F10", "menu"), ("Menu", "menu")):
            shortcuts.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(accel),
                                                    Gtk.NamedAction.new("term." + name)))
        self.add_controller(shortcuts)

        self.link_menu = Gio.Menu()
        self.link_menu.append(_("Open Link"), "term.open-link")
        self.menu = Gio.Menu()
        section = Gio.Menu()
        section.append(_("New Tab"), "win.new-tab")
        section.append(_("New Window"), "app.new-window")
        self.menu.append_section(None, section)
        section = Gio.Menu()
        section.append(_("Copy"), "term.copy")
        section.append(_("Copy Last Output"), "term.copy-last")
        section.append(_("Paste"), "term.paste")
        self.menu.append_section(None, section)
        section = Gio.Menu()
        section.append(_("Select All"), "term.select-all")
        section.append(_("Find…"), "term.find")
        self.menu.append_section(None, section)

    def on_context_menu(self, gesture, n_press, x, y):
        self.link = self.link_at(x, y)
        self.popup_menu(x, y)

    def link_at(self, x, y):
        """The link, if any, under this point of the terminal. Only the kinds we open are returned."""
        uri = self.terminal.check_hyperlink_at(x, y)
        if not uri:
            uri = self.terminal.check_match_at(x, y)[0]
            if uri and uri.lower().startswith("www."):
                uri = "http://" + uri
        return uri if uri and uri.split(":", 1)[0].lower() in LINK_SCHEMES else None

    def on_link_click(self, gesture, n_press, x, y):
        if not gesture.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return
        uri = self.link_at(x, y)
        if uri:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.open_link(uri)

    def open_link(self, uri):
        if uri:
            Gtk.UriLauncher.new(uri).launch(self.get_root(), None, self.on_link_opened)

    def on_link_opened(self, launcher, result):
        try:
            launcher.launch_finish(result)
        except GLib.Error:
            pass   # nothing to open it, or cancelled

    def popup_menu_at_cursor(self):
        """Shift+F10 or the Menu key: open the menu at the text cursor."""
        self.link = None
        column, row = self.terminal.get_cursor_position()
        height = self.terminal.get_char_height()
        self.popup_menu(TERMINAL_MARGIN + (column + 1) * self.terminal.get_char_width(),
                        TERMINAL_MARGIN + row * height, height)

    def on_menu_closed(self, popover, has_link):
        if has_link:
            self.menu.remove(0)   # the link entries are only in the menu of a link
        GLib.idle_add(popover.unparent)

    def popup_menu(self, x, y, height=1):
        self.actions.lookup_action("copy").set_enabled(self.terminal.get_has_selection())
        has_link = self.link is not None
        if has_link:
            self.menu.insert_section(0, None, self.link_menu)
        popover = Gtk.PopoverMenu.new_from_model(self.menu)
        popover.set_parent(self)
        popover.set_has_arrow(False)
        popover.set_halign(Gtk.Align.START)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, int(height)
        popover.set_pointing_to(rect)
        popover.connect("closed", self.on_menu_closed, has_link)
        popover.popup()
