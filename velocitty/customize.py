import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk
from xapp.util import l10n

from velocitty import GETTEXT_DOMAIN
from velocitty.palette import CUSTOM_COUNT

_ = l10n(GETTEXT_DOMAIN)


class CustomizePopover(Gtk.Popover):
    """Set the title and the color of a tab."""

    def __init__(self, tab, window):
        super().__init__()
        self.tab, self.window = tab, window

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=10, margin_bottom=10,
                      margin_start=10, margin_end=10)
        self.entry = Gtk.Entry(width_chars=28, placeholder_text=tab.auto_title,
                               text=tab.custom_title or "")
        self.entry.connect("changed", self.on_changed)
        self.entry.connect("activate", lambda e: self.popdown())
        box.append(self.entry)

        swatches = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        none = Gtk.ToggleButton(icon_name="xsi-edit-clear-symbolic", css_classes=["circular"],
                                valign=Gtk.Align.CENTER, tooltip_text=_("No Color"))
        none.set_active(tab.custom_color == 0)
        none.connect("toggled", self.on_color, 0)
        swatches.append(none)
        for index in range(1, CUSTOM_COUNT + 1):
            button = Gtk.ToggleButton(css_classes=["circular", "custom-swatch", "custom-%d" % index], group=none,
                                   valign=Gtk.Align.CENTER)
            button.set_active(tab.custom_color == index)
            button.connect("toggled", self.on_color, index)
            swatches.append(button)
        box.append(swatches)

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
            self.window.refresh()  # the headerbar, the tab bar and the overview

    def on_changed(self, entry):
        """The title applies as you type. An empty field brings back the automatic title."""
        text = entry.get_text()
        self.tab.custom_title = text if text.strip() else None
        self.tab.update_title()
        self.window.queue_restyle()
