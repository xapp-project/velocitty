import os
import shlex
import socket

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte
from xapp.util import l10n

from velocitty import GETTEXT_DOMAIN
from velocitty.palette import rgba
from velocitty.searchbar import SearchBar
from velocitty.shell import classify, shorten_path, strip_local_host, untrim_path

_ = l10n(GETTEXT_DOMAIN)

MAX_LAST_OUTPUT_ROWS = 10000
MODIFIER_KEYS = {Gdk.KEY_Shift_L, Gdk.KEY_Shift_R, Gdk.KEY_Control_L, Gdk.KEY_Control_R,
                 Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_Meta_L, Gdk.KEY_Meta_R, Gdk.KEY_Super_L,
                 Gdk.KEY_Super_R, Gdk.KEY_ISO_Level3_Shift, Gdk.KEY_Caps_Lock, Gdk.KEY_Num_Lock}
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
        self.custom_title = restore.get("title") or None   # a title the user chose
        self.custom_color = restore.get("color") or 0      # a custom color the user chose: 0 for none, else its number
        self.restored_command = restore.get("command")     # typed at the prompt, for the user to run
        self.last_cwd = cwd
        self.auto_title = _("Terminal")
        self.kind = "idle"
        self.command_line = ""
        self.title = self.full_title = _("Terminal")   # in the tab bar, and in full
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
        self.search_bar.connect("closed", self.on_search_closed)
        self.content.append(self.search_bar)

        self.terminal.connect("child-exited", lambda *_: self.window.close_tab(self))
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
        self.setup_actions()

        env = ["%s=%s" % item for item in os.environ.items() if item[0] not in ("TERM", "PROMPT_DIRTRIM")]
        env += ["TERM=xterm-256color", "COLORTERM=truecolor",
                "PROMPT_DIRTRIM=%d" % self.settings.get_int("prompt-folders")]
        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT, cwd or GLib.get_home_dir(), command or self.shell_argv(), env,
            GLib.SpawnFlags.DEFAULT, None, None, -1, None, self.on_spawned)
        GLib.timeout_add(500, self.poll)

    def shell_argv(self):
        shell = self.settings.get_string("shell")
        if not shell or not os.access(shell, os.X_OK):
            shell = os.environ.get("SHELL", "/bin/bash")
        # The command to run at start doesn't apply when the tab was asked to run a specific command
        startup = self.settings.get_string("startup-command").strip()
        if startup and self.runs_startup:
            # Run it in an interactive shell (so aliases and the usual setup are there), then
            # replace that shell with a normal one: no prompt is shown for the command.
            return [shell, "-i", "-c", "%s\n exec %s" % (startup, shlex.quote(shell))]
        return [shell]

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
            # The columns and rows change with the zoom, that is not a resize to show
            self.quiet_until = GLib.get_monotonic_time() + 500000

    def font_name(self):
        if not self.settings.get_boolean("use-system-font"):
            return self.settings.get_string("font")
        try:
            return Gio.Settings.new("org.gnome.desktop.interface").get_string("monospace-font-name")
        except GLib.Error:
            return "Monospace 11"

    def apply_colors(self, p):
        palette = [rgba(p["Color%d" % (i + 1)]) for i in range(16)]
        self.terminal.set_colors(rgba(p["Foreground"]), rgba(p["Background"]), palette)

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
        self.first_tick = None
        self.quiet_until = 0
        self.size_hide_source = 0
        # Called on every frame while the tab is on screen
        self.terminal.add_tick_callback(self.on_tick)

    def on_tick(self, widget, clock):
        size = (self.terminal.get_column_count(), self.terminal.get_row_count())
        now = GLib.get_monotonic_time()
        if self.first_tick is None:
            self.first_tick = now
        if size != self.last_size:
            self.last_size = size
            # The window settles to its size right after it appears, only show real resizes
            if now - self.first_tick < SETTLE_USEC:
                return GLib.SOURCE_CONTINUE
            if now < self.quiet_until:
                return GLib.SOURCE_CONTINUE
            self.show_indicator("%d \u00d7 %d" % size)
        return GLib.SOURCE_CONTINUE

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

    def on_spawned(self, terminal, pid, error):
        self.shell_pid = pid
        if self.restored_command and not error:
            # Typed, not run: the user presses Enter if they want it again
            GLib.timeout_add(300, self.type_restored_command)

    def on_key_pressed(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.used = True   # something was run here, even if it is over by now
        if self.restored_command and keyval not in MODIFIER_KEYS:
            self.forget_restored_command()
        return False

    def forget_restored_command(self):
        """The user is typing: the command that was waiting at the prompt is no longer remembered."""
        if self.restored_command:
            self.restored_command = None
            self.update_title()
            self.window.title_changed(self)

    def type_restored_command(self):
        if self.restored_command and self.get_root() is not None:
            self.terminal.feed_child(self.restored_command.encode())
        return GLib.SOURCE_REMOVE

    def is_worthy(self):
        """Something about this tab is worth keeping a window for: a title or color the user
        gave it, or something that is running (or waiting to be run) in it."""
        return bool(self.custom_title or self.custom_color or self.restored_command or self.kind != "idle")

    def is_clean_at(self, path):
        """A tab nobody has touched, sitting in this directory: nothing was customized and no
        command is waiting or running."""
        if (self.started_with_command or self.used or self.custom_title or self.custom_color
                or self.restored_command or self.kind != "idle"):
            return False
        directory = self.current_directory() or self.last_cwd
        return bool(directory) and os.path.realpath(directory) == os.path.realpath(path)

    def snapshot(self):
        """What is needed to bring this tab back."""
        directory = self.current_directory()
        if directory:
            self.last_cwd = directory
        command = self.command_line if self.kind != "idle" else self.restored_command
        if not command or "\n" in command or "\r" in command:
            command = None
        return {"cwd": self.last_cwd, "title": self.custom_title, "color": self.custom_color,
                "command": command}

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
            if self.kind != "idle":
                self.restored_command = None   # something was run, the typed command is no longer waiting
            self.update_title()
            self.window.refresh()
        return GLib.SOURCE_CONTINUE

    # -- title -------------------------------------------------------------

    def style_class(self):
        """The CSS class that colors this tab: the color of its session (ssh, admin, a
        running program) when it has one, else the custom color the user chose, if any."""
        if self.kind in ("remote", "admin") or (
                self.kind == "active" and self.settings.get_boolean("highlight-running")):
            return self.kind
        return "custom-%d" % self.custom_color if self.custom_color else None

    def update_title(self):
        """The automatic title is in full in the tooltip and the headerbar; the tab shows a folder
        shortened to its last folders, as many as the settings say."""
        shown = strip_local_host(self.terminal.get_window_title() or _("Terminal"))
        if self.kind in ("idle", "active"):
            shown = untrim_path(shown, self.shell_pid)
        full = shown
        short = shorten_path(shown, self.settings.get_int("tab-folders"))
        if self.kind == "active" and self.command_line:
            full += " — " + self.command_line
            short += " — " + self.command_line
        elif self.kind == "idle" and self.restored_command:
            # The command waiting at the prompt, until something is run
            full = short = "… " + self.restored_command
        self.auto_title = full
        self.title = self.custom_title or short
        self.full_title = self.custom_title or full
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
        self.forget_restored_command()
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
        self.quiet_until = GLib.get_monotonic_time() + 500000   # the terminal changes size, not a resize to show
        self.search_bar.open()

    def on_search_closed(self, bar):
        self.quiet_until = GLib.get_monotonic_time() + 500000

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
        self.popup_menu(x, y)

    def popup_menu_at_cursor(self):
        """Shift+F10 or the Menu key: open the menu at the text cursor."""
        column, row = self.terminal.get_cursor_position()
        height = self.terminal.get_char_height()
        self.popup_menu(TERMINAL_MARGIN + (column + 1) * self.terminal.get_char_width(),
                        TERMINAL_MARGIN + row * height, height)

    def popup_menu(self, x, y, height=1):
        self.actions.lookup_action("copy").set_enabled(self.terminal.get_has_selection())
        popover = Gtk.PopoverMenu.new_from_model(self.menu)
        popover.set_parent(self)
        popover.set_has_arrow(False)
        popover.set_halign(Gtk.Align.START)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, int(height)
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()
