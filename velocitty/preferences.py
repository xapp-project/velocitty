import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gio, GLib, Gtk, Pango
from xapp.util import l10n

from velocitty import GETTEXT_DOMAIN
from velocitty.palette import display_name, list_palettes
from velocitty.preview import PalettePreview
from velocitty.shell import installed_shells

_ = l10n(GETTEXT_DOMAIN)


def choice_row(settings, key, title, choices):
    """A drop-down for a key of any type. choices is [(value, label), ...]"""
    values = [value for value, _label in choices]
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new([label for _value, label in choices]))
    current = settings.get_value(key)
    row.set_selected(values.index(current.unpack()) if current.unpack() in values else 0)

    def selected(row, _param):
        settings.set_value(key, GLib.Variant(current.get_type_string(), values[row.get_selected()]))

    row.connect("notify::selected", selected)
    return row


def switch_row(settings, key, title, subtitle=""):
    row = Adw.SwitchRow(title=title, subtitle=subtitle)
    settings.bind(key, row, "active", Gio.SettingsBindFlags.DEFAULT)
    return row


def spin_row(settings, key, title, low, high, step=1):
    row = Adw.SpinRow.new_with_range(low, high, step)
    row.set_title(title)
    row.set_value(settings.get_int(key))

    def changed(row, _param):
        settings.set_int(key, int(row.get_value()))

    row.connect("notify::value", changed)
    return row


def spin_double_row(settings, key, title, low, high, step):
    row = Adw.SpinRow.new_with_range(low, high, step)
    row.set_title(title)
    row.set_digits(1)
    row.set_value(settings.get_double(key))

    def changed(row, _param):
        settings.set_double(key, round(row.get_value(), 2))

    row.connect("notify::value", changed)
    return row


def entry_row(settings, key, title, placeholder):
    """A row with a text entry, which can show an example while it is empty (Adw.EntryRow can't)."""
    row = Adw.ActionRow(title=title, activatable=False)
    entry = Gtk.Entry(placeholder_text=placeholder, valign=Gtk.Align.CENTER, width_chars=28)
    settings.bind(key, entry, "text", Gio.SettingsBindFlags.DEFAULT)
    row.add_suffix(entry)
    row.set_activatable_widget(entry)
    return row


class Preferences(Adw.Window):
    """A window rather than a dialog, so the terminals stay usable while it is open."""

    def __init__(self, settings, parent):
        super().__init__(title=_("Preferences"), modal=False, transient_for=parent,
                         default_width=920, default_height=640)

        # Pages on the right, one row per page in a sidebar on the left
        self.stack = Adw.ViewStack()
        self.sidebar = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.sidebar.connect("row-selected", self.on_row_selected)

        sidebar_toolbar = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=self.sidebar))
        sidebar_toolbar.add_top_bar(Adw.HeaderBar(show_end_title_buttons=False))
        sidebar_page = Adw.NavigationPage(title=_("Preferences"), child=sidebar_toolbar)

        self.content_title = Adw.WindowTitle(title="")
        content_toolbar = Adw.ToolbarView(content=self.stack)
        content_toolbar.add_top_bar(Adw.HeaderBar(title_widget=self.content_title, show_start_title_buttons=False))
        self.content_page = Adw.NavigationPage(title="", child=content_toolbar)

        split = Adw.NavigationSplitView(sidebar=sidebar_page, content=self.content_page,
                                        min_sidebar_width=180, max_sidebar_width=220)
        self.set_content(split)

        escape = Gtk.ShortcutController()
        escape.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                                         action=Gtk.CallbackAction.new(self.on_escape)))
        self.add_controller(escape)

        page = Adw.PreferencesPage(title=_("Appearance"), icon_name="xsi-appearance-symbolic")

        group = Adw.PreferencesGroup(title=_("Palette"))
        group.add(self.palette_chooser(settings))
        page.add(group)

        group = Adw.PreferencesGroup(title=_("Colors"))
        group.add(switch_row(settings, "highlight-running", _("Highlight Running Commands"),
                             _("Color the tab and the headerbar while a command is running")))
        group.add(switch_row(settings, "bold-is-bright", _("Bright Colors for Bold")))
        page.add(group)

        group = Adw.PreferencesGroup(title=_("Font"))
        group.add(self.font_row(settings))
        page.add(group)

        group = Adw.PreferencesGroup()
        group.add(spin_double_row(settings, "line-spacing", _("Line Spacing"), 1.0, 2.0, 0.1))
        group.add(spin_double_row(settings, "column-spacing", _("Column Spacing"), 1.0, 2.0, 0.1))
        page.add(group)

        group = Adw.PreferencesGroup(title=_("Cursor"))
        group.add(choice_row(settings, "cursor-shape", _("Cursor Shape"), [
            ("block", _("Block")), ("ibeam", _("I-Beam")), ("underline", _("Underline"))]))
        group.add(choice_row(settings, "cursor-blink", _("Cursor Blinking"), [
            ("system", _("Follow System")), ("on", _("Enabled")), ("off", _("Disabled"))]))
        page.add(group)
        self.add_page(page, "appearance")

        page = Adw.PreferencesPage(title=_("Behavior"), icon_name="xsi-applications-system-symbolic")
        group = Adw.PreferencesGroup(title=_("Windows and Tabs"))
        group.add(spin_row(settings, "default-columns", _("Default Columns"), 20, 400))
        group.add(spin_row(settings, "default-rows", _("Default Rows"), 5, 200))
        group.add(switch_row(settings, "restore-session", _("Restore Session"),
                             _("Reopen the windows and tabs of the last session at start")))
        page.add(group)

        group = Adw.PreferencesGroup(title=_("Shortened Paths"),
                                     description=_("The prompt is only shortened in new tabs."))
        folders = [(0, _("Don't Shorten")), (1, _("Last Folder")), (2, _("Last Two Folders")),
                   (3, _("Last Three Folders"))]
        group.add(choice_row(settings, "tab-folders", _("Tabs"), folders))
        group.add(choice_row(settings, "prompt-folders", _("Prompt"), folders))
        page.add(group)

        group = Adw.PreferencesGroup(title=_("Terminal"))
        group.add(switch_row(settings, "visual-bell", _("Visual Bell"),
                             _("Flash the headerbar when a program rings the bell")))
        page.add(group)

        group = Adw.PreferencesGroup(title=_("Scrolling"))
        group.add(choice_row(settings, "scrollbars", _("Use Scrollbars"), [
            ("system", _("Follow System")), ("always", _("Always")), ("never", _("Never"))]))
        page.add(group)
        group = Adw.PreferencesGroup()
        group.add(switch_row(settings, "scroll-on-keystroke", _("Scroll on Keystroke"),
                             _("Scroll to the bottom upon input keystroke")))
        group.add(switch_row(settings, "scroll-on-output", _("Scroll on Output"),
                             _("Scroll to the bottom when new content is available")))
        page.add(group)
        group = Adw.PreferencesGroup()
        group.add(switch_row(settings, "limit-scrollback", _("Limit Scrollback"),
                             _("Restrict the number of lines saved for scrollback")))
        lines = spin_row(settings, "scrollback-lines", _("Scrollback Lines"), 0, 1000000, 1000)
        lines.set_subtitle(_("The number of lines to keep for scrollback"))
        settings.bind("limit-scrollback", lines, "sensitive", Gio.SettingsBindFlags.GET)
        group.add(lines)
        page.add(group)

        group = Adw.PreferencesGroup(title=_("Shell"))
        shells = [("", _("System Default"))] + [(path, name) for path, name in installed_shells()]
        current = settings.get_string("shell")
        if current and current not in [path for path, _name in shells]:
            shells.append((current, current))  # no longer installed, keep showing the choice
        group.add(choice_row(settings, "shell", _("Shell"), shells))

        lines = entry_row(settings, "startup-command", _("Startup Command"), "fastfetch; echo;")
        lines.set_subtitle(_("Command to Run at Start"))
        group.add(lines)
        page.add(group)
        self.add_page(page, "behavior")
        self.sidebar.select_row(self.sidebar.get_row_at_index(0))

    def font_row(self, settings):
        """The "Use System Font" switch, with the custom font in its expanded part."""
        row = Adw.ExpanderRow(title=_("Use System _Font"), use_underline=True)
        switch = Gtk.Switch(halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        settings.bind("use-system-font", switch, "active", Gio.SettingsBindFlags.DEFAULT)
        switch.bind_property("active", row, "expanded",
                             GObject.BindingFlags.SYNC_CREATE | GObject.BindingFlags.BIDIRECTIONAL |
                             GObject.BindingFlags.INVERT_BOOLEAN)
        row.add_action(switch)

        font_name = Gtk.Label(hexpand=True, xalign=1, ellipsize=Pango.EllipsizeMode.END)
        settings.bind("font", font_name, "label", Gio.SettingsBindFlags.GET)
        suffix = Gtk.Box(spacing=12, hexpand=True)
        suffix.append(font_name)
        suffix.append(Gtk.Image(icon_name="xsi-go-next-symbolic"))
        custom = Adw.ActionRow(title=_("Custom Font"), activatable=True)
        custom.add_suffix(suffix)
        custom.connect("activated", lambda *_: self.choose_font(settings))
        row.add_row(custom)
        return row

    def choose_font(self, settings):
        dialog = Gtk.FontDialog(title=_("Select Font"))
        initial = Pango.FontDescription.from_string(settings.get_string("font") or "Monospace 10")

        def done(dialog, result):
            try:
                description = dialog.choose_font_finish(result)
            except Exception:
                return  # cancelled
            settings.set_string("font", description.to_string())

        dialog.choose_font(self, initial, None, done)

    def palette_chooser(self, settings):
        """One preview per palette, click to choose."""
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, homogeneous=True, halign=Gtk.Align.START,
                           column_spacing=8, row_spacing=8,
                           min_children_per_line=3, max_children_per_line=3)
        current = settings.get_string("palette")
        for name in list_palettes():
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6,
                          margin_start=6, margin_end=6)
            box.append(PalettePreview(name))
            box.append(Gtk.Label(label=display_name(name)))
            child = Gtk.FlowBoxChild(child=box)
            child.palette_name = name
            flow.append(child)
            if name == current:
                flow.select_child(child)

        def activated(flow, child):
            settings.set_string("palette", child.palette_name)

        flow.connect("child-activated", activated)
        return flow

    def on_escape(self, widget, args):
        self.close()
        return True

    def add_page(self, page, name):
        self.stack.add_named(page, name)
        row = Adw.ActionRow(title=page.get_title())
        row.add_prefix(Gtk.Image(icon_name=page.get_icon_name()))
        row.name = name
        self.sidebar.append(row)

    def on_row_selected(self, listbox, row):
        if row is not None:
            self.stack.set_visible_child_name(row.name)
            self.content_title.set_title(row.get_title())
            self.content_page.set_title(row.get_title())
