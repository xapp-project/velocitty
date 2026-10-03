# Terminal Velocitty 🖥️🚲🏙️

Terminal emulator for Linux desktops.

## Philosophy

- Intuitive
- Just enough configuration to feel at home
- Simple and easy to use
- Lets you quickly identify/organize/restore your important tabs

Velocitty was inspired by two fantastic projects:

- Ghostty
- Ptyxis

Before writing Velocitty from scratch, the team started improving Ptyxis. That work is available at
https://github.com/linuxmint/ptyxis-minted. When Ptyxis gets active again, we're hoping it can pick
and choose what it likes from that. As the number of changes became more and more important we
wanted to be able to drop features we didn't care about (profiles, containers, etc.), to not be tied
to a particular desktop, and to work in a language we were more comfortable with. So we made Velocitty.

## Palettes

Palettes are read from `~/.local/share/velocitty/palettes/` and `/usr/share/velocitty/palettes/`.

## Building from source

### For Mint with mint-dev-tools

```bash
# Install mint-dev-tools
apt install mint-dev-tools
# Remove any previous versions
apt remove 'velocitty*'
# Build and install from github
mint-build -i -g https://github.com/xapp-project/velocitty.git
```

### For Debian distributions (Mint, Ubuntu, etc.) with dpkg-buildpackage

```bash
# Get the source code..
git clone https://github.com/xapp-project/velocitty.git
# Go in..
cd velocitty
# Install the build dependencies..
sudo apt build-dep --mark-auto .
# Remove any previously built packages
rm -f ../velocitty*.deb
# Build
dpkg-buildpackage
# Install
sudo apt install ../velocitty*.deb
```

### For other distributions with meson

```bash
# Get the source code..
git clone https://github.com/xapp-project/velocitty.git
# Go in..
cd velocitty
```

Install the build and runtime dependencies for your distribution. For example:

```bash
# Fedora: sudo dnf install meson ninja-build python3 gettext
# Arch: sudo pacman -S meson ninja python gettext
# openSUSE: sudo zypper install meson ninja python3 gettext-tools
```

The dependencies are listed below (using debian pkg names, names may be different in your distribution). Install all of them.

#### Dependencies

```text
gir1.2-adw-1
gir1.2-gtk-4.0
gir1.2-vte-3.91
python3-gi
python3-setproctitle
python3-xapp
```

#### Dependencies for building

```text
gettext
libglib2.0-dev or libgio-2.0-dev
meson
pkg-config
```

#### Dependencies for runtime

```text
xapp-symbolic-icons
```

#### Build and install

```bash
meson setup build --prefix=/usr/local
meson compile -C build
sudo meson install -C build
```

#### Uninstall

To remove a Meson installation while retaining the build directory:

```bash
sudo ninja -C build uninstall
```

## Translations

Please use Launchpad to translate this project: https://translations.launchpad.net/linuxmint/latest/.

The PO files in this project are imported from there.

## License

Code: GPLv3+
