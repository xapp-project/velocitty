import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk
from xapp.util import l10n

from velocitty import GETTEXT_DOMAIN
from velocitty.palette import CUSTOM_COUNT

_ = l10n(GETTEXT_DOMAIN)


class CustomizePopover(Gtk.Popover):
    """Give a tab a name, a color or a command, which saves it, and set it to open at launch."""

    def __init__(self, tab, window):
        super().__init__()
        self.tab, self.window = tab, window

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12, width_request=280)
        self.entry = Gtk.Entry(placeholder_text=_("Name"), text=tab.custom_title or "")
        self.entry.connect("changed", self.on_changed)
        self.entry.connect("activate", lambda e: self.popdown())
        box.append(self.entry)

        self.command = Gtk.Entry(placeholder_text=_("Command to run when opened"), text=tab.run_command or "")
        self.command.connect("changed", self.on_command_changed)
        self.command.connect("activate", lambda e: self.popdown())
        box.append(self.command)

        swatches = Gtk.Box(homogeneous=True)
        none = Gtk.ToggleButton(icon_name="xsi-edit-clear-symbolic", css_classes=["circular"],
                                halign=Gtk.Align.CENTER, tooltip_text=_("No Color"))
        none.set_active(tab.custom_color == 0)
        none.connect("toggled", self.on_color, 0)
        swatches.append(none)
        for index in range(1, CUSTOM_COUNT + 1):
            button = Gtk.ToggleButton(css_classes=["circular", "custom-swatch", "custom-%d" % index], group=none,
                                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            button.set_active(tab.custom_color == index)
            button.connect("toggled", self.on_color, index)
            swatches.append(button)
        box.append(swatches)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        text.append(Gtk.Label(label=_("Startup Tab"), xalign=0))
        text.append(Gtk.Label(label=_("Opens when the terminal launches"), xalign=0,
                              css_classes=["dim-label", "caption"]))
        startup = Gtk.Switch(active=tab.startup, valign=Gtk.Align.CENTER)
        startup.connect("notify::active", lambda switch, _param: window.set_startup(tab, switch.get_active()))
        row = Gtk.Box(spacing=12)
        row.append(text)
        row.append(startup)
        box.append(Gtk.Separator())
        box.append(row)

        self.set_child(box)
        self.connect("closed", self.on_closed)
        self.connect("map", lambda *_: self.entry.grab_focus())

    def on_closed(self, popover):
        # The focus would go back to the tab we were attached to, and show its focus ring
        GLib.idle_add(self.finish)

    def finish(self):
        self.unparent()
        self.tab.terminal.grab_focus()
        return GLib.SOURCE_REMOVE

    def on_color(self, button, index):
        if button.get_active():
            self.tab.custom_color = index
            self.window.update_saved(self.tab)
            self.window.refresh()  # the headerbar, the tab bar and the overview

    def on_command_changed(self, entry):
        self.tab.run_command = entry.get_text().strip() or None
        self.window.update_saved(self.tab)

    def on_changed(self, entry):
        """The title applies as you type. An empty field brings back the automatic title."""
        text = entry.get_text()
        self.tab.custom_title = text if text.strip() else None
        self.tab.update_title()
        self.window.update_saved(self.tab)
        self.window.queue_restyle()
