import itertools
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from xapp.util import l10n

from velocitty import GETTEXT_DOMAIN
from velocitty.custom_tabs_page import CustomTabsPage
from velocitty.customize import CustomizePopover
from velocitty.palette import CUSTOM_CLASSES, STATES
from velocitty.tab import TermTab

_ = l10n(GETTEXT_DOMAIN)


def walk(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from walk(child)
        child = child.get_next_sibling()


def existing_directory(path):
    """The directory itself, else the nearest parent that still exists, else home."""
    while path and path != os.sep:
        if os.path.isdir(path):
            return path
        path = os.path.dirname(path)
    return GLib.get_home_dir()


class TermWindow(Adw.ApplicationWindow):
    serials = itertools.count()

    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.force_close = False
        self.closing = False
        self.serial = next(TermWindow.serials)
        self.restyle_queued = False
        self.bell_source = 0
        self.add_css_class("terminal")
        self.set_title(_("Terminal"))

        self.tab_view = Adw.TabView()
        # No shortcuts to go to or move a tab to the first or last position
        self.tab_view.set_shortcuts(Adw.TabViewShortcuts.ALL_SHORTCUTS & ~(
            Adw.TabViewShortcuts.CONTROL_HOME | Adw.TabViewShortcuts.CONTROL_END
            | Adw.TabViewShortcuts.CONTROL_SHIFT_HOME | Adw.TabViewShortcuts.CONTROL_SHIFT_END))
        self.selected_page = None   # the tab in front, and the one that was in front before it
        self.previous_page = None
        self.tab_view.connect("notify::selected-page", self.on_page_selected)
        self.tab_view.connect("notify::n-pages", self.on_n_pages)
        self.tab_view.connect("close-page", self.on_close_page)
        self.tab_view.connect("page-attached", self.on_page_attached)
        # Dragging a tab out of the bar makes a window for it
        self.tab_view.connect("create-window", self.on_create_window)

        self.window_title = Adw.WindowTitle(title=_("Terminal"), subtitle="")
        self.header = Adw.HeaderBar(title_widget=self.window_title)
        new_tab = Gtk.Button(icon_name="xsi-tab-new-symbolic", tooltip_text=_("New Tab"))
        new_tab.set_action_name("win.new-tab")
        self.header.pack_start(new_tab)
        self.custom_tabs_button = Gtk.Button(icon_name="xsi-user-bookmarks-symbolic", tooltip_text=_("Custom Tabs"),
                                             action_name="win.custom-tabs", visible=False)
        self.header.pack_start(self.custom_tabs_button)

        # Shown in full screen only, in place of the window buttons, to leave it
        self.unfullscreen_button = Gtk.Button(icon_name="xsi-view-restore-symbolic", action_name="win.fullscreen",
                                              tooltip_text=_("Leave Full Screen"), visible=False)
        self.header.pack_end(self.unfullscreen_button)

        menu = Gio.Menu()
        style_section = Gio.Menu()
        style_item = Gio.MenuItem.new(None, None)
        style_item.set_attribute_value("custom", GLib.Variant.new_string("style"))
        style_section.append_item(style_item)
        menu.append_section(None, style_section)
        zoom_section = Gio.Menu()
        zoom_item = Gio.MenuItem.new(None, None)
        zoom_item.set_attribute_value("custom", GLib.Variant.new_string("zoom"))
        zoom_section.append_item(zoom_item)
        menu.append_section(None, zoom_section)
        section = Gio.Menu()
        section.append(_("New Tab"), "win.new-tab")
        section.append(_("New Window"), "app.new-window")
        menu.append_section(None, section)
        section = Gio.Menu()
        section.append(_("Preferences"), "app.preferences")
        section.append(_("Keyboard Shortcuts"), "win.shortcuts")
        section.append(_("About"), "app.about")
        menu.append_section(None, section)

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.add_child(self.build_style_controls(), "style")
        popover.add_child(self.build_zoom_controls(), "zoom")
        self.menu_button = Gtk.MenuButton(icon_name="xsi-open-menu-symbolic", popover=popover)
        self.header.pack_end(self.menu_button)

        self.overview = Adw.TabOverview(enable_new_tab=True, view=self.tab_view)
        self.overview.connect("create-tab", self.on_overview_create_tab)
        self.overview.connect("notify::open", lambda *_: self.queue_restyle())
        self.header.pack_end(Adw.TabButton(view=self.tab_view, action_name="win.overview"))

        self.tab_bar = Adw.TabBar(view=self.tab_view, autohide=True)
        self.toolbar = Adw.ToolbarView(content=self.tab_view)
        self.toolbar.add_top_bar(self.header)
        self.toolbar.add_top_bar(self.tab_bar)
        self.overview.set_child(self.toolbar)
        # Right-clicking a tab, in the bar or in the overview, edits it
        for host in (self.tab_bar, self.overview):
            click = Gtk.GestureClick(button=3, propagation_phase=Gtk.PropagationPhase.CAPTURE)
            click.connect("pressed", self.on_tab_right_click, host)
            host.add_controller(click)
        self.customize_popover = None
        self.nav = Adw.NavigationView(pop_on_escape=True)
        self.nav.add(Adw.NavigationPage(child=self.overview, title=_("Terminal"), tag="terminal"))
        self.custom_tabs_page = CustomTabsPage(self)
        self.nav.connect("popped", lambda *_: self.focus_terminal())
        self.set_content(self.nav)
        self.custom_tabs_handler = self.app.custom_tabs.connect("changed", lambda *_: self.on_custom_tabs_changed())
        self.on_custom_tabs_changed()

        # In full screen the bars hide after a while and come back at the top edge
        self.bars_source = 0
        self.pointer_y = -1.0
        self.connect("notify::fullscreened", self.on_fullscreen_changed)
        motion = Gtk.EventControllerMotion(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", self.on_motion)
        self.add_controller(motion)

        for name, handler in {
            "new-tab": lambda *_: self.new_tab(),
            "close-tab": lambda *_: self.close_tab(self.current_tab()),
            "overview": lambda *_: self.toggle_overview(),
            "zoom-in": lambda *_: self.zoom(1),
            "zoom-out": lambda *_: self.zoom(-1),
            "zoom-reset": lambda *_: self.zoom(0),
            "fullscreen": lambda *_: self.toggle_fullscreen(),
            "customize-tab": lambda *_: self.customize_tab(),
            "custom-tabs": lambda *_: self.show_custom_tabs(),
            "main-menu": lambda *_: self.menu_button.popup(),
            "last-tab": lambda *_: self.switch_to_previous(),
            "shortcuts": lambda *_: self.get_application().show_shortcuts(),
        }.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

        self.connect("map", lambda *_: GLib.idle_add(self.focus_terminal))
        self.connect("close-request", self.on_close_request)

    # -- tabs --------------------------------------------------------------

    def new_tab(self, command=None, cwd=None, restore=None, at_end=False):
        current = self.current_tab()
        if cwd is None and current is not None:
            cwd = current.current_directory()
        tab = TermTab(self, self.app.settings, command, cwd, restore)
        tab.apply_colors(self.app.scheme())
        selected = self.tab_view.get_selected_page()
        page = self.tab_view.add_page(tab, None)
        page.set_title(_("Terminal"))
        if at_end:
            self.tab_view.reorder_page(page, self.tab_view.get_n_pages() - 1)
        elif selected is not None:
            # Next to the current tab
            self.tab_view.reorder_page(page, self.tab_view.get_page_position(selected) + 1)
        self.tab_view.set_selected_page(page)
        tab.terminal.grab_focus()
        if restore:
            tab.update_title()
            self.title_changed(tab)
        return page

    def on_create_window(self, view):
        """A tab was dragged out of the bar: it gets a window of its own."""
        return self.app.empty_window().tab_view

    def on_tab_right_click(self, gesture, n_press, x, y, host):
        widget = host.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None and widget.get_css_name() not in ("tab", "tabthumbnail"):
            widget = widget.get_parent()
        page = widget.get_property("page") if widget is not None else None
        if page is not None and isinstance(page.get_child(), TermTab):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.customize_tab(page.get_child())

    def on_custom_tabs_changed(self):
        self.custom_tabs_button.set_visible(bool(self.app.custom_tabs.entries))
        self.custom_tabs_page.refresh()

    def show_custom_tabs(self):
        if self.nav.get_visible_page() is self.custom_tabs_page:
            self.nav.pop()
        elif self.app.custom_tabs.entries:
            self.overview.set_open(False)
            self.custom_tabs_page.refresh()
            self.nav.push(self.custom_tabs_page)

    def set_startup(self, tab, startup):
        tab.startup = startup
        self.save_tab(tab)
        self.queue_restyle()

    def toggle_overview(self):
        self.overview.set_open(not self.overview.get_open())

    def on_close_page(self, view, page):
        tab = page.get_child()
        if isinstance(tab, TermTab) and not self.closing:
            self.record(tab)
        return False   # the tab is closed as usual

    def save_tab(self, tab):
        tab.entry = self.app.custom_tabs.save_open(tab.entry, tab.snapshot())

    def update_saved(self, tab):
        if tab.entry is not None or tab.custom_title or tab.custom_color or tab.run_command:
            self.save_tab(tab)

    def remove_tab(self, entry):
        tab = self.app.tab_for(entry)
        if tab is not None:
            tab.entry = None
        self.app.custom_tabs.remove(entry)
        if tab is not None:
            tab.window.close_tab(tab)

    def record(self, tab):
        if tab.entry is not None:
            self.app.custom_tabs.close(tab.entry, tab.snapshot())
        tab.entry = None

    def record_tabs(self):
        # The first tab goes in last, to end up first in the list
        for tab in reversed(self.tabs()):
            self.record(tab)

    def reopen(self, entry):
        page = self.new_tab(None, existing_directory(entry.get("cwd")), restore=entry, at_end=True)
        page.get_child().entry = entry
        return page

    def open_custom_tabs(self, entries):
        pages = [self.reopen(entry) for entry in entries if not self.app.is_open(entry)]
        self.app.custom_tabs.changed()
        self.nav.pop()
        if pages:
            self.tab_view.set_selected_page(pages[0])

    def on_page_attached(self, view, page, position):
        # A tab can come from another window
        tab = page.get_child()
        tab.window = self
        self.show_title(page, tab)
        self.queue_restyle()

    def on_page_selected(self, *_):
        page = self.tab_view.get_selected_page()
        if page is not self.selected_page:
            self.previous_page = self.selected_page
            self.selected_page = page
        self.refresh()

    def switch_to_previous(self):
        """Back to the tab that was in front before this one, to go back and forth between two."""
        page = self.previous_page
        if page is not None and page.get_child() is not None and self.tab_view.get_page_position(page) >= 0:
            self.tab_view.set_selected_page(page)

    def close_tab(self, tab):
        if tab is None or getattr(tab, "closing", False):
            return   # the shell exiting after the tab was closed by hand
        tab.closing = True
        page = self.tab_view.get_page(tab)
        if page is not None:
            self.tab_view.close_page(page)

    def focus_terminal(self):
        tab = self.current_tab()
        if tab is not None:
            tab.terminal.grab_focus()
        return GLib.SOURCE_REMOVE

    def current_tab(self):
        page = self.tab_view.get_selected_page()
        return page.get_child() if page else None

    def tabs(self):
        return [self.tab_view.get_nth_page(i).get_child() for i in range(self.tab_view.get_n_pages())]

    def on_n_pages(self, *_):
        if self.tab_view.get_n_pages() == 0:
            self.force_close = True
            self.close()

    def on_overview_create_tab(self, overview):
        # Add a tab but stay in the overview, on the tab we were on. libadwaita
        # would select the page we return and close the overview.
        selected = self.tab_view.get_selected_page()
        self.new_tab()
        if selected is not None:
            self.tab_view.set_selected_page(selected)
        return None

    # -- state -------------------------------------------------------------

    def apply_scheme(self):
        for tab in self.tabs():
            tab.apply_colors(self.app.scheme())

    def apply_settings(self):
        for tab in self.tabs():
            tab.apply_settings()
            tab.update_title()
        self.queue_restyle()
        self.refresh()

    def show_title(self, page, tab):
        """The tab bar shows the short title, the tooltip has the whole one."""
        page.set_title(tab.title)
        page.set_tooltip(tab.auto_title)

    def title_changed(self, tab):
        if not tab.is_ancestor(self.tab_view):
            return   # the tab is being moved to another window, it is brought up to date when it lands
        self.show_title(self.tab_view.get_page(tab), tab)
        if tab is self.current_tab():
            self.window_title.set_title(tab.full_title)

    def build_style_controls(self):
        """System, light and dark, side by side at the top of the menu."""
        box = Gtk.Box(css_classes=["linked"], homogeneous=True, margin_top=6, margin_bottom=6,
                      margin_start=12, margin_end=12)
        buttons = {}
        group = None
        for value, icon, tooltip in (("system", "xsi-computer-symbolic", _("Follow System Style")),
                                     ("light", "xsi-weather-clear-symbolic", _("Light Style")),
                                     ("dark", "xsi-weather-clear-night-symbolic", _("Dark Style"))):
            button = Gtk.ToggleButton(icon_name=icon, tooltip_text=tooltip, group=group)
            group = group or button
            buttons[value] = button
            box.append(button)

        def update(*_args):
            current = self.app.settings.get_string("color-scheme")
            buttons.get(current, buttons["system"]).set_active(True)

        def toggled(button, value):
            if button.get_active():
                self.app.settings.set_string("color-scheme", value)

        for value, button in buttons.items():
            button.connect("toggled", toggled, value)
        self.app.settings.connect("changed::color-scheme", update)
        update()
        return box

    def build_zoom_controls(self):
        """"- 100% +" for the menu: the percentage resets the zoom."""
        box = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
        zoom_out = Gtk.Button(icon_name="xsi-zoom-out-symbolic", action_name="win.zoom-out",
                              css_classes=["flat", "circular"], tooltip_text=_("Zoom Out"))
        zoom_in = Gtk.Button(icon_name="xsi-zoom-in-symbolic", action_name="win.zoom-in",
                             css_classes=["flat", "circular"], tooltip_text=_("Zoom In"))
        label = Gtk.Button(action_name="win.zoom-reset", css_classes=["flat"], hexpand=True,
                           tooltip_text=_("Reset Zoom"))

        def update(*_args):
            label.set_label("%d%%" % round(self.app.settings.get_double("zoom") * 100))

        self.app.settings.connect("changed::zoom", update)
        update()
        for widget in (zoom_out, label, zoom_in):
            box.append(widget)
        return box

    def zoom(self, step):
        tab = self.current_tab()
        if tab is not None:
            tab.zoom(step)

    def toggle_fullscreen(self):
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    # -- full screen -------------------------------------------------------

    def on_fullscreen_changed(self, *_args):
        fullscreen = self.is_fullscreen()
        self.unfullscreen_button.set_visible(fullscreen)
        # No window buttons in full screen, the button above leaves it
        self.header.set_show_start_title_buttons(not fullscreen)
        self.header.set_show_end_title_buttons(not fullscreen)
        # The bars float over the terminal in full screen, so showing them doesn't resize it
        self.toolbar.set_extend_content_to_top_edge(fullscreen)
        self.show_bars()

    def show_bars(self):
        self.toolbar.set_reveal_top_bars(True)
        if self.bars_source:
            GLib.source_remove(self.bars_source)
            self.bars_source = 0
        if self.is_fullscreen():
            self.bars_source = GLib.timeout_add(3000, self.hide_bars)

    def hide_bars(self):
        self.bars_source = 0
        if not self.is_fullscreen():
            return GLib.SOURCE_REMOVE
        over_bars = 0 <= self.pointer_y <= self.toolbar.get_top_bar_height()
        if self.menu_button.get_active() or over_bars:
            # Still in use, look again in a moment
            self.bars_source = GLib.timeout_add(1000, self.hide_bars)
            return GLib.SOURCE_REMOVE
        self.toolbar.set_reveal_top_bars(False)
        return GLib.SOURCE_REMOVE

    def on_motion(self, controller, x, y):
        self.pointer_y = y
        if not self.is_fullscreen():
            return
        # The top edge, or the bars themselves while they are showing
        reach = 6
        if self.toolbar.get_reveal_top_bars():
            reach = max(reach, self.toolbar.get_top_bar_height())
        if y <= reach:
            self.show_bars()

    def customize_tab(self, tab=None):
        """Edit the title, color and startup of a tab (the current one by default). Asking again
        for the current tab closes the editor."""
        if self.customize_popover is not None:
            self.customize_popover.popdown()
            if tab is None:
                return
        tab = tab or self.current_tab()
        if tab is None:
            return
        anchor = self.anchor_for(tab)
        # Attach the editor to a container and point at the anchor, not to the anchor itself:
        # a popover inside a tab makes libadwaita draw a focus ring around the tab while it is open.
        if self.overview.get_open():
            host = self.overview
        elif anchor is self.header:
            host = self.header
        else:
            host = self.tab_bar
        popover = CustomizePopover(tab, self)
        self.customize_popover = popover
        popover.connect("closed", lambda p: setattr(self, "customize_popover", None) if self.customize_popover is p else None)
        popover.set_parent(host)
        ok, bounds = anchor.compute_bounds(host)
        if ok:
            rect = Gdk.Rectangle()
            rect.x, rect.y = int(bounds.get_x()), int(bounds.get_y())
            rect.width, rect.height = int(bounds.get_width()), int(bounds.get_height())
            popover.set_pointing_to(rect)
        popover.popup()

    def anchor_for(self, tab):
        """The widget to attach the editor to: the tab in the tab bar or, in the overview,
        its thumbnail (the bars below it are not on screen), else the headerbar."""
        if self.overview.get_open():
            for widget in walk(self.overview):
                if widget.get_css_name() == "tabthumbnail" and widget.get_mapped():
                    page = widget.get_property("page")
                    if page is not None and page.get_child() is tab:
                        return widget
        for widget in walk(self.tab_bar):
            if widget.get_css_name() == "tab":
                page = widget.get_property("page")
                if page is not None and page.get_child() is tab and widget.get_mapped():
                    return widget
        return self.header

    def flash_headerbar(self):
        """The visual bell: the headerbar briefly takes the color of the active state,
        the one it has while a program runs."""
        self.add_css_class("bell")
        if self.bell_source:
            GLib.source_remove(self.bell_source)
        self.bell_source = GLib.timeout_add(120, self.end_flash)

    def end_flash(self):
        self.bell_source = 0
        self.remove_css_class("bell")
        return GLib.SOURCE_REMOVE

    def refresh(self):
        tab = self.current_tab()
        if tab is not None:
            self.tab_view.get_page(tab).set_needs_attention(False)
        for state in STATES + CUSTOM_CLASSES + ("running",):
            self.remove_css_class(state)
        if tab is not None:
            if tab.style_class():
                self.add_css_class(tab.style_class())
            if tab.is_running():
                self.add_css_class("running")
            self.window_title.set_title(tab.full_title)
        self.queue_restyle()

    def queue_restyle(self):
        if not self.restyle_queued:
            self.restyle_queued = True
            GLib.idle_add(self.restyle_widgets)

    def restyle_widgets(self):
        """Give each tab and overview thumbnail the CSS class of its session
        type, so it can be styled."""
        self.restyle_queued = False
        for root in (self.tab_bar, self.overview):
            for widget in walk(root):
                if widget.get_css_name() not in ("tab", "tabthumbnail"):
                    continue
                for state in STATES + CUSTOM_CLASSES + ("running", "startup"):
                    widget.remove_css_class(state)
                page = widget.get_property("page")
                tab = page.get_child() if page else None
                if isinstance(tab, TermTab) and tab.style_class():
                    widget.add_css_class(tab.style_class())
                if isinstance(tab, TermTab) and tab.is_running():
                    widget.add_css_class("running")
                if isinstance(tab, TermTab) and tab.startup:
                    widget.add_css_class("startup")
        return GLib.SOURCE_REMOVE

    # -- closing -----------------------------------------------------------

    def on_close_request(self, window):
        busy = [tab for tab in self.tabs() if tab.kind != "idle"]
        if self.force_close or not busy:
            self.closing = True
            self.record_tabs()
            self.app.custom_tabs.disconnect(self.custom_tabs_handler)
            return False

        dialog = Adw.AlertDialog(heading=_("Close Window?"), body=_("Some processes are still running."))
        dialog.add_response("cancel", _("_Cancel"))
        dialog.add_response("close", _("_Close"))
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        # Escape and Enter cancel: closing is the one that must be asked for
        dialog.set_close_response("cancel")
        dialog.set_default_response("cancel")
        group = Adw.PreferencesGroup()
        group.add_css_class("close-list")
        for tab in busy:
            row = Adw.ActionRow(title=GLib.markup_escape_text(tab.command_line or tab.title))
            row.add_css_class("close-" + (tab.style_class() or tab.kind))
            group.add(row)
        dialog.set_extra_child(group)
        dialog.connect("response", self.on_close_response)
        dialog.present(self)
        return True

    def on_close_response(self, dialog, response):
        if response == "close":
            self.force_close = True
            self.close()
