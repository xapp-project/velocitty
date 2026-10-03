import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Graphene, Gtk, Pango

from velocitty.palette import CUSTOM_COUNT, load_palette, rgba, state_colors

WIDTH, HEIGHT = 210, 118
PADDING = 10
FONT = "Monospace 9"


def rect(x, y, w, h):
    r = Graphene.Rect()
    r.init(x, y, w, h)
    return r


class PalettePreview(Gtk.Widget):
    """A small picture of a palette: a prompt, a few colored words, the 16 colors, and along
    the top the session colors and the six colors a tab can be given. It shows the current
    light or dark variant."""

    def __init__(self, palette_name):
        super().__init__(css_classes=["palette-preview"], overflow=Gtk.Overflow.HIDDEN)
        self.set_size_request(WIDTH, HEIGHT)
        self.palette = load_palette(palette_name)
        self.style_manager = Adw.StyleManager.get_default()
        self.style_manager.connect("notify::dark", lambda *_: self.queue_draw())

    def variant(self):
        return self.palette["Dark" if self.style_manager.get_dark() else "Light"]

    def draw_text(self, snapshot, x, y, segments, p):
        """Draw (text, color name) pieces one after the other, return the end x."""
        for text, key in segments:
            layout = self.create_pango_layout(text)
            layout.set_font_description(Pango.FontDescription.from_string(FONT))
            snapshot.save()
            snapshot.translate(Graphene.Point().init(x, y))
            snapshot.append_layout(layout, rgba(p[key]))
            snapshot.restore()
            x += layout.get_pixel_size()[0]
        return x

    def do_snapshot(self, snapshot):
        p = self.variant()
        snapshot.append_color(rgba(p["Background"]), rect(0, 0, WIDTH, HEIGHT))

        # Along the top: the session colors (ssh and root) and
        # then the six colors a tab can be given
        chip_w, chip_h, space = 10, 6, 3
        x = WIDTH - PADDING - 2 * (chip_w + space) - 8 - CUSTOM_COUNT * (chip_w + space) + space
        for state in ("remote", "admin"):
            snapshot.append_color(rgba(state_colors(p)[state][0]), rect(x, PADDING, chip_w, chip_h))
            x += chip_w + space
        x += 8 - space
        for index in range(1, CUSTOM_COUNT + 1):
            snapshot.append_color(rgba(p["Custom%d" % index]), rect(x, PADDING, chip_w, chip_h))
            x += chip_w + space

        self.draw_text(snapshot, PADDING, PADDING + 8, [
            ("user@host", "Color11"), (":", "Foreground"), ("~", "Color13"), ("$ ls", "Foreground")], p)
        self.draw_text(snapshot, PADDING, PADDING + 26, [
            ("Documents", "Color13"), (" ", "Foreground"), ("run.sh", "Color11"),
            (" ", "Foreground"), ("notes.txt", "Foreground")], p)
        self.draw_text(snapshot, PADDING, PADDING + 44, [
            ("error", "Color10"), ("  ", "Foreground"), ("warning", "Color12"), ("  ", "Foreground"),
            ("info", "Color15")], p)

        # The sixteen colors
        size, gap = 14, 4
        top = HEIGHT - PADDING - 2 * size - gap
        for index in range(16):
            column, row = index % 8, index // 8
            snapshot.append_color(rgba(p["Color%d" % (index + 1)]),
                                  rect(PADDING + column * (size + gap), top + row * (size + gap), size, size))
