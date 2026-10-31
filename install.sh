#!/bin/bash
# =============================================================================
#  LamaDeck installer (formerly Ollama Manager).
#
#  WHAT: installs Python dependencies, copies the app to a user directory
#        and adds a menu entry (Utility category). Detects an existing
#        install - asks about updating (newer version in the repo),
#        reinstalling (same version), or warns before overwriting a newer
#        installed version with an older one. A separate flag removes the app.
#  WHY:  everything lands in $HOME - no sudo, no touching the system beyond
#        one .desktop file, per the "no root where avoidable" principle. The
#        one exception: three system packages (curl, pkexec, python3-pip)
#        that need apt/root if missing - offered with an explicit, confirmed
#        prompt each time, never installed silently.
#
#        If the app is already installed from the .deb package, it offers to
#        remove that first, so there aren't two copies and two menu entries.
#
#  Run:      ./install.sh
#  Uninstall: ./install.sh --uninstall
# =============================================================================
set -euo pipefail

TARGET_DIR="$HOME/.local/share/lamadeck"
DESKTOP_FILE="$HOME/.local/share/applications/lamadeck.desktop"
# WHY: standard place for a user's own app icons - KDE finds it by name (Icon=lamadeck)
ICON_FILE="$HOME/.local/share/icons/hicolor/256x256/apps/lamadeck.png"
# WHY: until 0.9.x the app was called "Ollama Manager" and installed under these names -
#      removed on install/uninstall, so the menu doesn't show the old app next to the new one.
#      The app itself moves the user's settings and conversations on its first start.
LEGACY_DIR="$HOME/.local/share/ollama-manager"
LEGACY_DESKTOP_FILE="$HOME/.local/share/applications/ollama-manager.desktop"
LEGACY_ICON_FILE="$HOME/.local/share/icons/hicolor/256x256/apps/ollama-manager.png"
# WHY: 0.6.6 installed a vector icon here - KDE prefers scalable icons, so a leftover
#      one would keep showing the old icon. Removed on every install/uninstall.
OLD_ICON_FILE="$HOME/.local/share/icons/hicolor/scalable/apps/ollama-manager.svg"
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
SOURCE_SCRIPT="$SCRIPT_DIR/lamadeck.py"
SOURCE_LANG_DIR="$SCRIPT_DIR/lang"
SOURCE_THEME="$SCRIPT_DIR/theme.qss"
SOURCE_FONTS_DIR="$SCRIPT_DIR/fonts"
SOURCE_ICON="$SCRIPT_DIR/logo.png"
TARGET_SCRIPT="$TARGET_DIR/lamadeck.py"
LEGACY_SCRIPT="$LEGACY_DIR/ollama_manager.py"

_version_from_file() {
    # WHAT: extracts the value of the WERSJA constant from the given app script.
    sed -n 's/^WERSJA = "\(.*\)"/\1/p' "$1" 2>/dev/null || true
}

_compare_versions() {
    # WHAT: "older"/"same"/"newer" for version $1 relative to $2.
    # WHY:  plain string comparison would misorder e.g. "0.3.9" and "0.3.10"
    #       (alphabetically "9" > "1") - sort -V understands version numbers.
    if [ "$1" = "$2" ]; then
        echo "same"
    elif [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" = "$1" ]; then
        echo "older"
    else
        echo "newer"
    fi
}

_confirm_apt_install() {
    # WHAT: asks before installing a system package via apt (needs sudo).
    # WHY:  install.sh otherwise never touches anything outside $HOME - this
    #       is the one exception, so it must be an explicit, confirmed choice
    #       every time, never a silent sudo call. Used for both curl and
    #       python3-pip below.
    local package="$1"
    local reason="$2"
    echo "$reason"
    read -r -p "Install '$package' now via 'sudo apt install $package'? [Y/n] " ANSWER || true
    case "$ANSWER" in
        [nN]*) return 1 ;;
        *) sudo apt install -y "$package" ;;
    esac
}

remove_legacy() {
    # WHAT: removes an "Ollama Manager" (pre-rename) install - files only, the
    #       settings and conversations stay (the app moves them on its first start).
    rm -rf "$LEGACY_DIR"
    rm -f "$LEGACY_DESKTOP_FILE" "$LEGACY_ICON_FILE"
}

remove_app() {
    if [ ! -d "$TARGET_DIR" ] && [ ! -f "$DESKTOP_FILE" ] && [ ! -d "$LEGACY_DIR" ]; then
        echo "LamaDeck is not installed - nothing to remove." >&2
        exit 1
    fi
    echo "Removing LamaDeck..."
    rm -rf "$TARGET_DIR"
    rm -f "$DESKTOP_FILE" "$ICON_FILE" "$OLD_ICON_FILE"
    remove_legacy
    # WHY: the app's own "start after login" option (Settings) writes this file -
    #      left behind, KDE would try to start a removed program at every login.
    rm -f "$HOME/.config/autostart/lamadeck.desktop" "$HOME/.config/autostart/ollama-manager.desktop"
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    echo "Done. LamaDeck has been removed (pip dependencies are kept - other tools may share them)."
    echo "Your settings and saved conversations are kept in ~/.config/LamaDeck and ~/.local/share/LamaDeck."
}

# --- Removal flag --------------------------------------------------------------
if [ "${1:-}" = "--uninstall" ]; then
    remove_app
    exit 0
fi

if [ ! -f "$SOURCE_SCRIPT" ]; then
    echo "Can't find lamadeck.py next to install.sh - run this script from the repo directory." >&2
    exit 1
fi

NEW_VERSION="$(_version_from_file "$SOURCE_SCRIPT")"

# --- Detecting the .deb package ----------------------------------------------------
# WHAT: if the app is already installed from the .deb package, warn and offer to
#       remove it first (default) or abort; installing anyway needs an explicit "c".
# WHY:  both installs at once mean two "LamaDeck" entries in the menu (one from
#       /usr/share/applications, one from ~/.local/share/applications) and two copies
#       of the app that update separately - confusing, and easy to miss.
# WHY: checks both package names - "ollama-manager" (until 0.9.x) and "lamadeck"
DEB_PACKAGE=""
for candidate in lamadeck ollama-manager; do
    if command -v dpkg-query >/dev/null 2>&1 \
        && [ "$(dpkg-query -W -f='${Status}' "$candidate" 2>/dev/null)" = "install ok installed" ]; then
        DEB_PACKAGE="$candidate"
        break
    fi
    if [ -f "/usr/share/applications/$candidate.desktop" ]; then
        DEB_PACKAGE="$candidate"
        break
    fi
done
if [ -n "$DEB_PACKAGE" ]; then
    DEB_VERSION="$(dpkg-query -W -f='${Version}' "$DEB_PACKAGE" 2>/dev/null || true)"
    echo "The app is already installed from the .deb package '$DEB_PACKAGE'${DEB_VERSION:+ (version $DEB_VERSION)}."
    echo "Installing it with install.sh as well would give two entries in the menu and two"
    echo "copies of the app that update separately."
    read -r -p "[R]emove the .deb version first (sudo apt remove $DEB_PACKAGE), [c]ontinue anyway, or [a]bort? [R/c/a] " ANSWER || true
    case "$ANSWER" in
        [cC]*) echo "Continuing - keep in mind there will be two menu entries." ;;
        [aA]*) echo "Aborted - nothing was changed."; exit 0 ;;
        *)
            sudo apt remove -y "$DEB_PACKAGE" || {
                echo "Removing the .deb package failed - nothing else was changed." >&2
                exit 1
            }
            ;;
    esac
fi

# --- Detecting an existing install ---------------------------------------------
# WHY: an "Ollama Manager" install (pre-rename) counts as the installed version too -
#      the update prompt then compares against it, and the old files are removed below.
if [ ! -f "$TARGET_SCRIPT" ] && [ -f "$LEGACY_SCRIPT" ]; then
    echo "Found the previous version of this app, installed as \"Ollama Manager\" - it will be replaced by LamaDeck."
    INSTALLED_FILE="$LEGACY_SCRIPT"
    LEGACY_FOUND=1
else
    INSTALLED_FILE="$TARGET_SCRIPT"
fi
if [ -f "$INSTALLED_FILE" ]; then
    INSTALLED_VERSION="$(_version_from_file "$INSTALLED_FILE")"
    COMPARISON="$(_compare_versions "$INSTALLED_VERSION" "$NEW_VERSION")"
    # WHY: the same version under the old name is still worth replacing (new name, new
    #      menu entry) - asked like an update (default Yes), not like a reinstall (default No)
    if [ "${LEGACY_FOUND:-0}" = 1 ] && [ "$COMPARISON" = "same" ]; then
        COMPARISON="older"
    fi
    case "$COMPARISON" in
        older)
            echo "Installed version: $INSTALLED_VERSION. Update available: $NEW_VERSION."
            read -r -p "Update now? [Y/n] " ANSWER || true
            case "$ANSWER" in
                [nN]*) echo "Cancelled." ; exit 0 ;;
            esac
            ;;
        same)
            echo "Version $NEW_VERSION is already installed."
            read -r -p "Reinstall it anyway? [y/N] " ANSWER || true
            case "$ANSWER" in
                [yY]*) ;;
                *) echo "Cancelled." ; exit 0 ;;
            esac
            ;;
        newer)
            echo "WARNING: the installed version ($INSTALLED_VERSION) is newer than the one in this repo ($NEW_VERSION)."
            read -r -p "Overwrite the newer version with the older one? [y/N] " ANSWER || true
            case "$ANSWER" in
                [yY]*) ;;
                *) echo "Cancelled." ; exit 0 ;;
            esac
            ;;
    esac
fi

echo "Installing LamaDeck $NEW_VERSION..."

# 1) Python dependencies - no root (--user)
install_python_deps() {
    # WHAT: skip pip entirely if PyQt6/requests are already importable (e.g. installed
    #       earlier via apt as python3-pyqt6/python3-requests) - nothing to do.
    if python3 -c "import PyQt6, requests" >/dev/null 2>&1; then
        echo "PyQt6 and requests are already available for python3 - skipping pip install."
        return
    fi

    if ! python3 -m pip --version >/dev/null 2>&1; then
        _confirm_apt_install python3-pip "pip is not available for python3 - it's needed to install PyQt6/requests." || {
            echo "Cancelled - pip is required to install PyQt6/requests." >&2
            exit 1
        }
        if ! python3 -m pip --version >/dev/null 2>&1; then
            echo "pip still not available after installing python3-pip." >&2
            exit 1
        fi
    fi

    echo "Installing PyQt6 and requests for the current user..."
    local pip_output
    if pip_output="$(python3 -m pip install --user --upgrade "${PIP_PACKAGES[@]}" 2>&1)"; then
        return
    fi

    # WHY: recent Debian (trixie/sid) marks the system python3 "externally-managed"
    #      (PEP 668), so a plain `pip install --user` aborts before installing anything.
    #      --break-system-packages is pip's own documented escape hatch for exactly
    #      this case; since we still only install with --user, nothing outside
    #      ~/.local is touched and no apt-managed package is overwritten.
    if printf '%s\n' "$pip_output" | grep -q "externally-managed-environment"; then
        echo "System Python is externally managed (PEP 668) - retrying with --break-system-packages (user-level install only)..."
        python3 -m pip install --user --upgrade --break-system-packages "${PIP_PACKAGES[@]}"
    else
        printf '%s\n' "$pip_output" >&2
        exit 1
    fi
}

command -v python3 >/dev/null 2>&1 || {
    echo "python3 not found. Install it first: sudo apt install python3" >&2
    exit 1
}

# WHY: not every system has curl by default, but the app's own install
#      buttons need it (Ollama's official installer, and the uv bootstrap
#      used for Open WebUI/LiteLLM) - offered now instead of failing later,
#      confusingly, from inside the running app.
command -v curl >/dev/null 2>&1 || {
    _confirm_apt_install curl \
        "curl is not installed - the app needs it for its own install buttons (Ollama, and uv for Open WebUI/LiteLLM)." \
        || echo "Skipping curl - those in-app install buttons won't work until it's installed."
}

# WHY: not every system has pkexec by default either, but the whole app relies
#      on it for every privileged action (service start/stop/autostart, the
#      Advanced tab's env vars) - it's its own package on modern Debian/Ubuntu
#      (polkit was split into polkitd/pkexec; policykit-1 is now just a
#      transitional metapackage), so plain "polkit" wouldn't be enough.
command -v pkexec >/dev/null 2>&1 || {
    _confirm_apt_install pkexec \
        "pkexec is not installed - the app needs it for every action that requires admin rights (start/stop the Ollama service, autostart, Advanced tab settings)." \
        || echo "Skipping pkexec - those actions won't work until it's installed."
}

# WHY: PyQt6 below 6.12 - 6.12 (October 2026) dropped the QtPdf module from the pip
#      wheel, and the chat needs it to read PDF attachments (same pin as build-appimage.sh)
PIP_PACKAGES=("PyQt6<6.12" "PyQt6-Qt6<6.12" requests)
install_python_deps

# WHAT: PDF attachments in the chat need the QtPdf module.
# WHY:  the pip PyQt6 wheel (below 6.12) already contains it, but Debian/Ubuntu ship it as a separate
#       package - so when PyQt6 came from apt, offer it here. Optional: without it
#       everything works except reading PDFs.
python3 -c "import PyQt6.QtPdf" >/dev/null 2>&1 || {
    _confirm_apt_install python3-pyqt6.qtpdf \
        "The QtPdf module is missing - the chat needs it to read PDF attachments (optional)." \
        || echo "Skipping QtPdf - PDF files can't be attached in the chat until it's installed."
}

# 2) Copy the script to a stable location (survives removing/moving the cloned repo)
mkdir -p "$TARGET_DIR"
cp "$SOURCE_SCRIPT" "$TARGET_SCRIPT"
chmod +x "$TARGET_SCRIPT"

# WHY: interface translations (lang/*.json) must live next to the script -
#      _KATALOG_LANG in the code is resolved relative to lamadeck.py, not the CWD.
if [ -d "$SOURCE_LANG_DIR" ]; then
    cp -r "$SOURCE_LANG_DIR" "$TARGET_DIR/"
fi

# WHY: the window stylesheet (dark Breeze from the mockups) is also loaded from
#      next to the script - without it the app falls back to the system theme.
if [ -f "$SOURCE_THEME" ]; then
    cp "$SOURCE_THEME" "$TARGET_DIR/"
fi

# WHY: bundled fonts (Inter + JetBrains Mono, SIL OFL) are loaded from next to the
#      script too - JetBrains Mono is rarely installed system-wide.
if [ -d "$SOURCE_FONTS_DIR" ]; then
    cp -r "$SOURCE_FONTS_DIR" "$TARGET_DIR/"
fi

# WHY: the app icon goes twice - next to the script (window icon, loaded by the
#      app itself) and into the icon theme folder (application menu / taskbar).
rm -f "$OLD_ICON_FILE"
if [ -f "$SOURCE_ICON" ]; then
    cp "$SOURCE_ICON" "$TARGET_DIR/"
    mkdir -p "$(dirname "$ICON_FILE")"
    cp "$SOURCE_ICON" "$ICON_FILE"
fi

# WHY: the pre-rename "Ollama Manager" files would leave a second, outdated entry in the menu
remove_legacy
# WHY: "start after login" from the old name would point to the removed script - rewritten
#      here under the new name (the app would do it on its first start, but only if it
#      gets started before the next login)
LEGACY_AUTOSTART="$HOME/.config/autostart/ollama-manager.desktop"
if [ -f "$LEGACY_AUTOSTART" ]; then
    cat > "$HOME/.config/autostart/lamadeck.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=LamaDeck
Exec="$(command -v python3)" "$TARGET_SCRIPT" --w-zasobniku
Icon=lamadeck
X-KDE-autostart-after=panel
Terminal=false
EOF
    rm -f "$LEGACY_AUTOSTART"
fi

# 3) Application menu entry - Categories=Utility is the "Utilities" section in KDE.
#    WHY Comment[pl]: the .desktop format supports per-language variants
#    directly - default (no suffix) in English, Polish as an explicit [pl] variant.
mkdir -p "$(dirname "$DESKTOP_FILE")"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=LamaDeck
GenericName=Local AI control panel
GenericName[pl]=Panel lokalnego AI
Comment=Control panel for your local AI stack: Ollama, LiteLLM, Open WebUI and chat
Comment[pl]=Panel lokalnego stosu AI: Ollama, LiteLLM, Open WebUI i czat
Exec=python3 "$TARGET_DIR/lamadeck.py"
Icon=lamadeck
Categories=Utility;
Terminal=false
StartupNotify=true
EOF

# WHY: refreshes the menu database right away - without this KDE sometimes
#      only picks up the new entry after the next login.
command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

# 4) Read access to the Ollama service log for the current user.
#    WHY: the app's Log screen reads 'journalctl -u ollama' (a system service).
#    Without the 'systemd-journal' (or 'adm') group journalctl silently returns
#    nothing, so the app had to ask for a password every time. Asked once here,
#    default Yes, via sudo in this terminal - same pattern as the apt prompts.
#    'id -nG "$USER"' reads the group database, not the current session, so a
#    user who was already added (but hasn't logged in again) isn't asked twice.
allow_service_log() {
    if id -nG "$USER" | tr ' ' '\n' | grep -qxE 'adm|systemd-journal|wheel'; then
        return
    fi
    if ! getent group systemd-journal >/dev/null 2>&1; then
        return
    fi
    read -r -p "Let $USER read the Ollama service log in the app (adds you to the 'systemd-journal' group via sudo)? [Y/n] " ANSWER || true
    case "$ANSWER" in
        [nN]*)
            echo "Skipped - you can still allow it later from the app's Log screen."
            return
            ;;
    esac
    if sudo usermod -a -G systemd-journal "$USER"; then
        echo "Done - the Ollama log will be visible in the app after you log out and log back in."
    else
        echo "Could not change your groups - you can allow it later from the app's Log screen." >&2
    fi
}
allow_service_log

echo "Done. LamaDeck $NEW_VERSION should now be in the application menu (Utilities)."
echo "If it doesn't show up right away, log out and log back in."
echo "To uninstall: ./install.sh --uninstall"
