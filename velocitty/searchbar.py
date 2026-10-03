import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Vte
from xapp.util import l10n

from velocitty import GETTEXT_DOMAIN

_ = l10n(GETTEXT_DOMAIN)

PCRE2_CASELESS = 0x00000008
PCRE2_MULTILINE = 0x00000400


class SearchBar(Gtk.Revealer):
    """A bar below a terminal to search its output and its scrollback. The terminal stays
    usable while it is open. The options are saved in the settings, so all the bars share them."""

    __gsignals__ = {"closed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, terminal, settings):
        super().__init__(visible=False, transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        self.terminal = terminal
        self.settings = settings
        self.searched = None   # the text of the search in progress

        self.entry = Gtk.SearchEntry(hexpand=True, placeholder_text=_("Search"))
        previous = Gtk.Button(icon_name="xsi-go-up-symbolic", tooltip_text=_("Previous Match"))
        following = Gtk.Button(icon_name="xsi-go-down-symbolic", tooltip_text=_("Next Match"))
        steps = Gtk.Box(css_classes=["linked"])
        steps.append(previous)
        steps.append(following)

        group = Gio.SimpleActionGroup()
        for key in ("search-match-case", "search-whole-words", "search-regex", "search-wrap"):
            group.add_action(settings.create_action(key))
        self.insert_action_group("search", group)
        options = Gio.Menu()
        options.append(_("Case Sensitive"), "search.search-match-case")
        options.append(_("Whole Word"), "search.search-whole-words")
        options.append(_("Wrap Around"), "search.search-wrap")
        options.append(_("Regular Expression"), "search.search-regex")
        cog = Gtk.MenuButton(menu_model=options, direction=Gtk.ArrowType.UP,
                             tooltip_text=_("Search Options"))
        cog_content = Gtk.Box(spacing=4)
        cog_content.append(Gtk.Image(icon_name="xsi-emblem-system-symbolic"))
        cog_content.append(Gtk.Image(icon_name="xsi-pan-up-symbolic"))
        cog.set_child(cog_content)
        close = Gtk.Button(icon_name="xsi-window-close-symbolic", tooltip_text=_("Close"),
                           css_classes=["circular"])

        box = Gtk.Box(spacing=6, css_classes=["search-bar"])
        for widget in (self.entry, steps, cog, close):
            box.append(widget)
        self.set_child(box)
        terminal.search_set_wrap_around(True)

        self.entry.connect("search-changed", self.on_edited)
        self.entry.connect("activate", lambda *_: self.search(backwards=True))
        self.entry.connect("stop-search", lambda *_: self.close())
        previous.connect("clicked", lambda *_: self.search(backwards=True))
        following.connect("clicked", lambda *_: self.search(backwards=False))
        close.connect("clicked", lambda *_: self.close())
        settings.connect("changed", self.on_option_changed)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key_pressed)
        self.entry.add_controller(keys)

    def open(self):
        """Show the bar, with the selected text, if it is a single line, ready to search for."""
        if self.terminal.get_has_selection():
            text = (self.terminal.get_text_selected(Vte.Format.TEXT) or "").strip()
            if text and "\n" not in text:
                self.entry.set_text(text)
        self.set_visible(True)
        self.set_reveal_child(True)
        self.entry.grab_focus()
        self.entry.select_region(0, -1)

    def close(self):
        self.set_reveal_child(False)
        self.set_visible(False)
        self.searched = None
        self.terminal.search_set_regex(None, 0)
        self.terminal.unselect_all()
        self.terminal.grab_focus()
        self.emit("closed")

    def on_key_pressed(self, controller, keyval, keycode, state):
        # Shift+Enter goes the other way
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.SHIFT_MASK:
            self.search(backwards=False)
            return True
        return False

    def on_option_changed(self, settings, key):
        # Only a search that was already made is redone, with the new options
        if key.startswith("search-") and self.get_visible() and self.searched:
            self.update_search()

    def on_edited(self, entry):
        # Nothing is searched while typing, only when Enter is pressed
        entry.remove_css_class("error")
        if not entry.get_text():
            self.searched = None
            self.terminal.search_set_regex(None, 0)
            self.terminal.unselect_all()

    def search(self, backwards):
        """Enter, or the arrows: search for what is in the entry, or go on to the next match."""
        if not self.entry.get_text():
            return
        if self.searched == self.entry.get_text():
            self.find(backwards)
        else:
            self.update_search(backwards)

    def update_search(self, backwards=True):
        """Search for what is in the entry, with the current options."""
        text = self.entry.get_text()
        self.searched = text
        self.entry.remove_css_class("error")
        self.terminal.search_set_wrap_around(self.settings.get_boolean("search-wrap"))
        pattern = text if self.settings.get_boolean("search-regex") else GLib.regex_escape_string(text, -1)
        if self.settings.get_boolean("search-whole-words"):
            pattern = "\\b(?:%s)\\b" % pattern
        flags = PCRE2_MULTILINE
        if not self.settings.get_boolean("search-match-case"):
            flags |= PCRE2_CASELESS
        try:
            regex = Vte.Regex.new_for_search(pattern, -1, flags)
        except GLib.Error:
            # An incomplete regular expression, while it is being typed
            self.terminal.search_set_regex(None, 0)
            self.entry.add_css_class("error")
            return
        self.terminal.search_set_regex(regex, 0)
        self.find(backwards)

    def find(self, backwards):
        if self.terminal.search_get_regex() is None:
            return
        found = self.terminal.search_find_previous() if backwards else self.terminal.search_find_next()
        if found:
            self.entry.remove_css_class("error")
        else:
            self.entry.add_css_class("error")
