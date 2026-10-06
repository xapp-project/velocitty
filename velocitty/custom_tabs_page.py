import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk
from xapp.util import l10n

from velocitty import GETTEXT_DOMAIN

_ = l10n(GETTEXT_DOMAIN)


class CustomTabsPage(Adw.NavigationPage):

    def __init__(self, window):
        super().__init__(title=_("Custom Tabs"), tag="custom-tabs")
        self.window = window
        self.custom_tabs = window.app.custom_tabs
        self.checks = []   # (check button, entry) of each row

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        # The bottom margin leaves room for the buttons that float over the page
        clamp = Adw.Clamp(maximum_size=640, child=self.list, margin_top=12, margin_bottom=84,
                          margin_start=12, margin_end=12, valign=Gtk.Align.START)
        scroller = Gtk.ScrolledWindow(child=clamp, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)

        self.open_button = Gtk.Button(label=_("Open"), css_classes=["suggested-action", "pill"], sensitive=False)
        self.open_button.connect("clicked", self.on_open)
        self.select_all = Gtk.Button(label=_("Select All"), css_classes=["pill"])
        self.select_all.connect("clicked", self.on_select_all)
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, valign=Gtk.Align.END, margin_bottom=24)
        buttons.append(self.select_all)
        buttons.append(self.open_button)
        page = Gtk.Overlay(child=scroller)
        page.add_overlay(buttons)

        view = Adw.ToolbarView(content=page, css_classes=["custom-tabs"])
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)

    def refresh(self):
        while (row := self.list.get_first_child()) is not None:
            self.list.remove(row)
        self.checks = []
        for entry in self.custom_tabs.entries:
            self.list.append(self.build_row(entry))
        self.update_buttons()
        if not self.custom_tabs.entries and self.window.nav.get_visible_page() is self:
            self.window.nav.pop()

    def build_row(self, entry):
        folder = entry["cwd"]
        home = GLib.get_home_dir()
        if folder == home or folder.startswith(home + os.sep):
            folder = "~" + folder[len(home):]
        title = entry.get("title") or os.path.basename(entry["cwd"].rstrip(os.sep)) or folder
        is_open = self.window.app.is_open(entry)

        row = Adw.ActionRow(title=GLib.markup_escape_text(title), subtitle=GLib.markup_escape_text(folder))
        if entry.get("color"):
            row.add_prefix(Gtk.Box(width_request=4, margin_top=6, margin_bottom=6,
                                   css_classes=["color-bar", "custom-%d" % entry["color"]]))
        check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        row.add_prefix(check)
        if is_open:
            check.set_active(True)
            check.set_sensitive(False)
            check.set_tooltip_text(_("Already open"))
        else:
            check.connect("toggled", lambda *_: self.update_buttons())
            row.set_activatable_widget(check)
            self.checks.append((check, entry))

        startup = Gtk.Switch(active=bool(entry.get("startup")), valign=Gtk.Align.CENTER,
                                tooltip_text=_("Opens when the terminal launches"))
        startup.connect("notify::active", lambda switch, _param: self.set_startup(entry, switch.get_active()))
        row.add_suffix(Gtk.Label(label=_("Startup Tab"), css_classes=["dim-label", "caption"]))
        row.add_suffix(startup)
        remove = Gtk.Button(icon_name="xsi-window-close-symbolic", valign=Gtk.Align.CENTER,
                            css_classes=["flat", "circular"], tooltip_text=_("Remove"))
        remove.connect("clicked", lambda button: self.window.remove_tab(entry))
        row.add_suffix(remove)
        return row

    def set_startup(self, entry, startup):
        tab = self.window.app.tab_for(entry)
        if tab is not None:
            tab.window.set_startup(tab, startup)
        else:
            self.custom_tabs.set_startup(entry, startup)

    def selected(self):
        return [entry for check, entry in self.checks if check.get_active()]

    def update_buttons(self):
        chosen = len(self.selected())
        self.open_button.set_sensitive(chosen > 0)
        self.select_all.set_label(_("Select None") if self.checks and chosen == len(self.checks) else _("Select All"))
        self.select_all.set_sensitive(bool(self.checks))

    def on_select_all(self, button):
        everything = len(self.selected()) != len(self.checks)
        for check, _entry in self.checks:
            check.set_active(everything)

    def on_open(self, button):
        self.window.open_custom_tabs(self.selected())
