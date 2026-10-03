import os
from string import Template

from gi.repository import Gdk, GLib

from velocitty.config import DATA_DIR

DEFAULT_PALETTE = "Espresso"
LIGHT_TEXT, DARK_TEXT = "#ffffff", "#1e1e1e"
MIN_TEXT_CONTRAST = 4.5   # WCAG AA for normal text
STATES = ("remote", "admin", "active")
CUSTOM_COUNT = 6   # Custom1 to Custom6, the colors a tab can be given
CUSTOM_CLASSES = tuple("custom-%d" % i for i in range(1, CUSTOM_COUNT + 1))


def palette_dirs():
    return [os.path.join(GLib.get_user_data_dir(), "velocitty", "palettes"),
            os.path.join(DATA_DIR, "palettes")]


def find_palette_file(name):
    for directory in palette_dirs():
        path = os.path.join(directory, name + ".palette")
        if os.path.exists(path):
            return path
    return None


def list_palettes():
    """The palette names (file names without the extension), sorted by the name each one shows."""
    names = set()
    for directory in palette_dirs():
        if os.path.isdir(directory):
            names.update(f[:-len(".palette")] for f in os.listdir(directory) if f.endswith(".palette"))
    return sorted(names, key=lambda n: display_name(n).lower())


def load_palette(name):
    """Read a palette into {"Light": {...}, "Dark": {...}}, falling back to
    the default one if it is missing or broken."""
    for candidate in (name, DEFAULT_PALETTE):
        path = find_palette_file(candidate)
        if path is None:
            continue
        try:
            return _read(path)
        except GLib.Error:
            continue
    raise RuntimeError("No usable palette found")


def _read(path):
    """Palettes have a Light and a Dark section, or just colors in [Palette]
    that are used for both."""
    keyfile = GLib.KeyFile()
    keyfile.load_from_file(path, GLib.KeyFileFlags.NONE)
    palette = {}
    for scheme in ("Light", "Dark"):
        section = scheme if keyfile.has_group(scheme) else "Palette"
        values = {key: keyfile.get_string(section, key) for key in keyfile.get_keys(section)[0]}
        palette[scheme] = _with_text_colors(values)
    return palette


def display_name(name):
    """The name a palette gives itself, or its file name."""
    path = find_palette_file(name)
    if path is not None:
        try:
            keyfile = GLib.KeyFile()
            keyfile.load_from_file(path, GLib.KeyFileFlags.NONE)
            return keyfile.get_string("Palette", "Name")
        except GLib.Error:
            pass
    return name


# The palette defines every color. What is left is the text on them, and the wash of a
# running program.

def _parse(color):
    return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _luminance(color):
    """The relative luminance of a color, from 0 (black) to 1 (white), as WCAG defines it."""
    def linear(channel):
        if channel <= 0.03928:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(channel) for channel in color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(color, other):
    """The contrast ratio between two colors, from 1 to 21."""
    lighter, darker = sorted((_luminance(color), _luminance(other)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _is_dark(color):
    """Dark enough for white text to read better on it than black text does."""
    return _luminance(color) < 0.179


def readable_text(background):
    """White or dark, whichever reads better on this background (a "#rrggbb" color)."""
    color = _parse(background)
    if _contrast(color, _parse(LIGHT_TEXT)) >= _contrast(color, _parse(DARK_TEXT)):
        return LIGHT_TEXT
    return DARK_TEXT


def _with_text_colors(values):
    v = dict(values)
    # On the titlebar, the palette's own text color, unless it can't be read there
    text = v["Foreground"]
    if _contrast(_parse(v["Titlebar"]), _parse(text)) < MIN_TEXT_CONTRAST:
        text = readable_text(v["Titlebar"])
    v["TitlebarText"] = text
    v["RemoteText"] = readable_text(v["Remote"])
    v["AdminText"] = readable_text(v["Admin"])
    # A program is running, and the bell: a faint wash over the titlebar, with its text
    v["ActiveForeground"] = text
    v["ActiveBackground"] = "rgba(255, 255, 255, 0.16)" if _is_dark(_parse(v["Background"])) else "rgba(0, 0, 0, 0.10)"
    return v


def rgba(value):
    color = Gdk.RGBA()
    color.parse(value)
    return color


def state_colors(p):
    """(background, foreground) of each session type."""
    return {
        "remote": (p["Remote"], p["RemoteText"]),
        "admin": (p["Admin"], p["AdminText"]),
        "active": (p["ActiveBackground"], p["ActiveForeground"]),
    }


# The CSS of a palette, in three pieces. The first is the same for every palette, the second is
# repeated for the session colors and for each custom color, the third is the visual bell.
# Only the terminal windows take the palette's colors: the preferences window and other
# dialogs keep the normal libadwaita look.
BASE_CSS = Template("""
/* The placeholder libadwaita draws for a tab that has no thumbnail yet looks up
   this named color on the tab itself, so it can only be set this way. It is only
   used by the tab overview. */
@define-color thumbnail_bg_color alpha($fg, 0.12);
window.terminal { background-color: $bg; }
.term-scroller { background-color: $bg; }
window.terminal headerbar, window.terminal tabbar .box, window.terminal .search-bar,
window.terminal revealer.top-bar > windowhandle { background-color: $tb; color: $tf; }
window.terminal headerbar:backdrop, window.terminal tabbar .box:backdrop,
window.terminal .search-bar:backdrop,
window.terminal revealer.top-bar > windowhandle:backdrop { background-color: $tb; }
window.terminal tabbar tab, window.terminal tabbar tabbox > tabboxchild { border-radius: 0; }
/* no gaps around the headerbar and the tab bar: the tabs fill the bar */
window.terminal tabbar tabbox { padding: 0; }
window.terminal tabbar .box { padding: 0; }
window.terminal tabbar tabbox > separator { margin: 0; min-width: 0; opacity: 0; }
window.terminal revealer.top-bar > windowhandle > box { padding: 0; margin: 0; border-spacing: 0; }
window.terminal revealer.top-bar > windowhandle { padding: 0; margin: 0; }
window.terminal toolbarview.overview { background-color: $ov; color: $fg; }
window.terminal toolbarview.overview headerbar,
window.terminal toolbarview.overview revealer.top-bar > windowhandle { background-color: $ov; color: $fg; }
window.terminal toolbarview.overview scrolledwindow, window.terminal toolbarview.overview statuspage,
window.terminal toolbarview.overview revealer.bottom-bar > windowhandle {
    background-color: transparent; color: $fg;
}
window.terminal toolbarview.overview tabthumbnail .tab-close-button { color: $fg; }
window.terminal toolbarview.overview tabthumbnail .tab-close-button > image {
    background-color: color-mix(in srgb, $fg 15%, transparent);
}
window.terminal toolbarview.overview tabthumbnail .tab-close-button:hover > image {
    background-color: color-mix(in srgb, $fg 25%, transparent);
}
window.terminal toolbarview.overview tabthumbnail .tab-close-button:active > image {
    background-color: color-mix(in srgb, $fg 55%, transparent);
}
window.terminal popover > contents { background-color: $tb; color: $tf; }
window.terminal popover > arrow { background-color: $tb; }
.close-list list, .close-list row { border-radius: 0; }
.palette-preview { border-radius: 8px; }
.custom-swatch { min-width: 22px; min-height: 22px; padding: 0; }
.size-indicator {
    color: $fg; padding: 4px 10px; border-radius: 8px;
    background-color: color-mix(in srgb, $bg 88%, transparent);
}
.search-bar { padding: 6px; }
""")

COLOR_CSS = Template("""
    /* the headerbar takes the color of the active tab's session... */
    window.$n headerbar { background-color: $bg; color: $fg; }
    window.$n headerbar:backdrop { background-color: $bg; }
    /* ...but the tab bar, the search bar and the overview header stay neutral */
    window.$n tabbar .box, window.$n .search-bar { background-color: $tb; color: $tf; }
    window.$n toolbarview.overview headerbar,
    window.$n toolbarview.overview revealer.top-bar > windowhandle {
        background-color: $ov; color: $fg0;
    }
    /* a flat bar along the bottom of each colored tab */
    tabbar tab.$n { box-shadow: inset 0 -3px 0 $bg; }
    toolbarview.overview tabthumbnail.$n { background-color: $bg; color: $fg; }
    row.close-$n { box-shadow: inset 4px 0 0 $bg; }
""")

# The visual bell flashes the headerbar with the color of the active state, and leaves the
# text color alone. It comes last, so it is on top of the other colors.
BELL_CSS = Template("""
window.terminal.bell headerbar { background-color: $active; }
""")

SWATCH_CSS = Template(".custom-swatch.custom-$index { background-color: $color; }\n")


def build_css(p):
    """CSS for one scheme of a palette."""
    titlebar, titlebar_text = p["Titlebar"], p["TitlebarText"]
    # The overview is a step away from the terminal background (lighter on dark
    # palettes, darker on light ones), so the thumbnails stand out against it.
    overview = "color-mix(in srgb, %s 90%%, %s)" % (p["Background"], p["Foreground"])

    css = BASE_CSS.substitute(bg=p["Background"], fg=p["Foreground"], tb=titlebar, tf=titlebar_text,
                              ov=overview)
    for index in range(1, CUSTOM_COUNT + 1):
        css += SWATCH_CSS.substitute(index=index, color=p["Custom%d" % index])

    # The colors the user can give a tab take the place of the session colors, so they
    # style the same things
    colors = dict(state_colors(p))
    for index in range(1, CUSTOM_COUNT + 1):
        color = p["Custom%d" % index]
        colors["custom-%d" % index] = (color, readable_text(color))

    for name, (background, text) in colors.items():
        css += COLOR_CSS.substitute(n=name, bg=background, fg=text, tb=titlebar, tf=titlebar_text,
                                    ov=overview, fg0=p["Foreground"])
    return css + BELL_CSS.substitute(active=p["ActiveBackground"])
