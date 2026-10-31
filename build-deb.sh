#!/bin/bash
# =============================================================================
#  Build a .deb package for LamaDeck (formerly Ollama Manager).
#
#  WHAT: packs lamadeck.py + lang/ + theme.qss + fonts/ into /usr/share, adds a launcher
#        script in /usr/bin and a .desktop entry in /usr/share/applications,
#        then assembles it all into a single .deb via dpkg-deb.
#  WHY:  a plain hand-written DEBIAN/control + dpkg-deb --build instead of
#        full debhelper/dh-python - this project is a single Python file,
#        it doesn't need a heavy packaging toolchain ("simplicity above all",
#        see CLAUDE.md). Dependencies go through apt (Depends), NOT pip like
#        install.sh - that's the standard on Debian/Ubuntu and avoids mixing
#        pip with the system Python.
#
#  Run: ./build-deb.sh
#  Output: lamadeck_<version>_all.deb in the repo directory.
#  Installing the result: sudo apt install ./lamadeck_<version>_all.deb
# =============================================================================
set -euo pipefail
# WHY: files in the package must be 644/755 (root-owned, not group-writable) - without this
#      a user umask of 002 leaked into the .deb as rw-rw-r--
umask 022

PACKAGE_NAME="lamadeck"
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
cd "$SCRIPT_DIR"

if [ ! -f lamadeck.py ]; then
    echo "Can't find lamadeck.py next to build-deb.sh - run this script from the repo directory." >&2
    exit 1
fi

if ! command -v dpkg-deb >/dev/null 2>&1; then
    echo "dpkg-deb not found - this script only works on Debian/Ubuntu (and derivatives)." >&2
    exit 1
fi

# WHY: package version ALWAYS synced with the WERSJA constant in the code -
#      a single source of truth, same as for any other version change.
VERSION="$(sed -n 's/^WERSJA = "\(.*\)"/\1/p' lamadeck.py)"
if [ -z "$VERSION" ]; then
    echo "Could not extract WERSJA from lamadeck.py" >&2
    exit 1
fi

echo "Building package ${PACKAGE_NAME} ${VERSION}..."

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

# --- Package layout -----------------------------------------------------------
mkdir -p "$BUILD_DIR/DEBIAN"
mkdir -p "$BUILD_DIR/usr/bin"
mkdir -p "$BUILD_DIR/usr/share/$PACKAGE_NAME"
mkdir -p "$BUILD_DIR/usr/share/applications"
mkdir -p "$BUILD_DIR/usr/share/icons/hicolor/256x256/apps"

# App + translations + stylesheet (see CLAUDE.md - lang/ and theme.qss MUST
# travel with the script, both are resolved relative to lamadeck.py's location)
cp lamadeck.py "$BUILD_DIR/usr/share/$PACKAGE_NAME/"
cp -r lang "$BUILD_DIR/usr/share/$PACKAGE_NAME/"
cp theme.qss "$BUILD_DIR/usr/share/$PACKAGE_NAME/"
# WHY: bundled Inter + JetBrains Mono (SIL OFL, licenses in fonts/) - loaded by the app
#      from next to the script, so the UI looks the same on every machine
cp -r fonts "$BUILD_DIR/usr/share/$PACKAGE_NAME/"
# WHY: app icon next to the script (window icon) and in the system icon theme (menu)
cp logo.png "$BUILD_DIR/usr/share/$PACKAGE_NAME/"
cp logo.png "$BUILD_DIR/usr/share/icons/hicolor/256x256/apps/$PACKAGE_NAME.png"

# WHAT: the package also adds the LamaDeck APT repository (see apt-repo.sh), like Chrome or
#       VS Code do - one .deb from GitHub is enough, later versions come with "apt upgrade".
# WHY:  only when the repository's public key is in the source tree (packaging/), i.e. after
#       "./apt-repo.sh init-key"; the source entry is a conffile - a user's edit survives updates.
KEYRING="packaging/lamadeck-archive-keyring.gpg"
if [ -f "$KEYRING" ]; then
    mkdir -p "$BUILD_DIR/usr/share/keyrings" "$BUILD_DIR/etc/apt/sources.list.d"
    cp "$KEYRING" "$BUILD_DIR/usr/share/keyrings/lamadeck-archive-keyring.gpg"
    cat > "$BUILD_DIR/etc/apt/sources.list.d/lamadeck.sources" <<EOF
Types: deb
URIs: https://cyryllo.github.io/LamaDeck/apt
Suites: stable
Components: main
Signed-By: /usr/share/keyrings/lamadeck-archive-keyring.gpg
EOF
    echo "/etc/apt/sources.list.d/lamadeck.sources" > "$BUILD_DIR/DEBIAN/conffiles"
else
    echo "Note: $KEYRING not found - the package won't add the APT repository (run ./apt-repo.sh init-key)."
fi

# WHY: a small wrapper instead of calling the full path straight from
#      .desktop - this also lets `lamadeck` be run from a terminal.
cat > "$BUILD_DIR/usr/bin/$PACKAGE_NAME" <<EOF
#!/bin/sh
exec python3 /usr/share/$PACKAGE_NAME/lamadeck.py "\$@"
EOF
chmod 755 "$BUILD_DIR/usr/bin/$PACKAGE_NAME"

# Menu entry - same category/icon as install.sh (own icon: logo.png). Comment[pl]: the .desktop
# format supports per-language variants directly (see install.sh).
cat > "$BUILD_DIR/usr/share/applications/$PACKAGE_NAME.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=LamaDeck
GenericName=Local AI control panel
GenericName[pl]=Panel lokalnego AI
Comment=Control panel for your local AI stack: Ollama, LiteLLM, Open WebUI and chat
Comment[pl]=Panel lokalnego stosu AI: Ollama, LiteLLM, Open WebUI i czat
Exec=$PACKAGE_NAME
Icon=$PACKAGE_NAME
Categories=Utility;
Terminal=false
StartupNotify=true
EOF

# --- Package metadata -----------------------------------------------------------
# WHY: Replaces/Conflicts ollama-manager - the package's name until 0.9.x; apt removes the
#      old package when this one is installed, so there's no second app in the menu.
# WHY: python3-pyqt6/python3-requests as Depends (apt), not pip - these are
#      the packages that provide the dependencies on Debian/Ubuntu (2026).
#      'pkexec' as its own package, NOT 'policykit-1' - polkit was split into
#      polkitd/pkexec, 'policykit-1' is now just a transitional metapackage.
#      'curl' as Depends too - not every system has it by default, but the
#      app's own install buttons need it (Ollama's official installer, and
#      the uv bootstrap used for Open WebUI/LiteLLM).
#      'python3-pyqt6.qtsvg' - on Debian QtSvg is a separate package, and the
#      app draws its sidebar/button icons from SVG shapes (QSvgRenderer).
#      'python3-pyqt6.qtpdf' - also separate on Debian; the chat reads the text
#      of attached PDF files with QtPdf.
cat > "$BUILD_DIR/DEBIAN/control" <<EOF
Package: $PACKAGE_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3, python3-pyqt6, python3-pyqt6.qtsvg, python3-pyqt6.qtpdf, python3-requests, pkexec, systemd, curl
Replaces: ollama-manager
Conflicts: ollama-manager
Maintainer: Cyryl Sochacki <cyrylsochacki@gmail.com>
Homepage: https://github.com/cyryllo/LamaDeck
Description: control panel for a local AI stack (Ollama, LiteLLM, Open WebUI)
 Unofficial desktop app (PyQt6, KDE) for a local AI stack: the Ollama service
 and models on several hosts, a LiteLLM router with one OpenAI-compatible
 address, Open WebUI, and a built-in chat with speed statistics, attachments
 and search in large files. Formerly called "Ollama Manager". Requires
 systemd and polkit; Ollama itself can be installed from the app.
EOF

# WHY: refresh the menu database after install/removal - the same thing
#      install.sh does by hand after copying .desktop, here as package
#      maintainer scripts (apt runs them automatically after install/removal).
cat > "$BUILD_DIR/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
EOF
chmod 755 "$BUILD_DIR/DEBIAN/postinst"

cat > "$BUILD_DIR/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database /usr/share/applications 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
EOF
chmod 755 "$BUILD_DIR/DEBIAN/postrm"

# --- Build ---------------------------------------------------------------------
# WHY: --root-owner-group records root:root ownership in the .deb without
#      needing to run this script under sudo (dpkg >= 1.19.0, long present
#      on Debian/Ubuntu).
DEB_FILE="${PACKAGE_NAME}_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$BUILD_DIR" "$DEB_FILE"

echo "Done: $DEB_FILE"
echo "Install with: sudo apt install ./$DEB_FILE"
