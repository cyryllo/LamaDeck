#!/bin/bash
# =============================================================================
#  LamaDeck AppImage builder.
#
#  WHAT: builds LamaDeck-<version>-x86_64.AppImage - one file that runs on any
#        current Linux distribution (Fedora, Arch, openSUSE, Ubuntu, Debian...).
#        Inside: a standalone Python (python-build-standalone), PyQt6 + requests
#        from pip, and the app itself (lamadeck.py, theme.qss, fonts/, lang/, logo).
#  WHY:  the .deb only covers Debian/Ubuntu. An AppImage has no sandbox (unlike
#        Flatpak), so systemctl, pkexec and journalctl keep working exactly as in
#        the installed app.
#
#  Usage:   ./build-appimage.sh
#  Needs:   bash, curl, tar; internet access (Python, wheels, appimagetool).
#           No root, no FUSE (appimagetool runs extracted).
#  Result:  LamaDeck-<version>-x86_64.AppImage next to this script.
#           Everything else stays in build/appimage (cache for the next build).
# =============================================================================
set -euo pipefail
umask 022

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
cd "$SCRIPT_DIR"

# WHY: pinned, so two builds of the same commit give the same app. To update:
#      https://github.com/astral-sh/python-build-standalone/releases (an
#      "install_only_stripped" x86_64-unknown-linux-gnu build of CPython 3.12).
PYTHON_RELEASE="20261026"
PYTHON_VERSION="3.12.16"
PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_RELEASE}/cpython-${PYTHON_VERSION}%2B${PYTHON_RELEASE}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"

VERSION="$(sed -n 's/^WERSJA = "\(.*\)"/\1/p' lamadeck.py)"
if [ -z "$VERSION" ]; then
    echo "Can't read WERSJA from lamadeck.py" >&2
    exit 1
fi

WORK_DIR="$SCRIPT_DIR/build/appimage"
CACHE_DIR="$WORK_DIR/cache"
APP_DIR="$WORK_DIR/LamaDeck.AppDir"
OUTPUT="$SCRIPT_DIR/LamaDeck-${VERSION}-x86_64.AppImage"

mkdir -p "$CACHE_DIR"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/usr/share/lamadeck"

# --- 1. Python --------------------------------------------------------------
# WHY: a standalone build, not the system Python - it doesn't depend on what the
#      user's distribution ships and is relocatable (works from any mount point).
PYTHON_TARBALL="$CACHE_DIR/python-${PYTHON_VERSION}-${PYTHON_RELEASE}.tar.gz"
if [ ! -f "$PYTHON_TARBALL" ]; then
    echo "Downloading Python ${PYTHON_VERSION}..."
    curl -fL --progress-bar -o "$PYTHON_TARBALL.part" "$PYTHON_URL"
    mv "$PYTHON_TARBALL.part" "$PYTHON_TARBALL"
fi
tar -xzf "$PYTHON_TARBALL" -C "$APP_DIR/usr"   # unpacks to usr/python
PY="$APP_DIR/usr/python/bin/python3"

# --- 2. Libraries -----------------------------------------------------------
# WHY: --only-binary - nothing is compiled; PyQt6 wheels bring their own Qt
#      (including QtSvg, QtPdf and the Wayland/X11 platform plugins).
# WHY: PyQt6 pinned to 6.11 - 6.12 (October 2026) no longer ships the QtPdf module,
#      which the chat needs to read PDF attachments
# WHY: PyQt6-WebEngine for the file preview in the chat ("Page" view - web pages and games),
#      same 6.11 line as PyQt6
echo "Installing PyQt6, PyQt6-WebEngine and requests..."
"$PY" -s -m pip install --quiet --no-cache-dir --only-binary=:all: \
    --disable-pip-version-check "PyQt6==6.11.*" "PyQt6-Qt6==6.11.*" \
    "PyQt6-WebEngine==6.11.*" "PyQt6-WebEngine-Qt6==6.11.*" requests

# WHAT: drops parts of Qt and Python the app never uses.
# WHY:  a much smaller file. The app imports Widgets, Gui, Svg, Pdf, DBus, Network and
#       WebEngineWidgets. WebEngine itself needs the Qml, Quick, QuickWidgets, WebChannel,
#       Positioning and OpenGL LIBRARIES (checked with ldd), so those stay - only their
#       Python bindings go. The ldd check below stops the build if anything is missing.
SITE="$("$PY" -s -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
QT="$SITE/PyQt6/Qt6"
rm -rf "$QT/qml" 2>/dev/null || true
# WHY: the QtWebChannel and QtPositioning bindings stay - PyQt6.QtWebEngineWidgets imports them
for pattern in Quick Qml Designer Multimedia SpatialAudio 3D Charts DataVis Graphs RemoteObjects \
               Sensors SerialPort Bluetooth Nfc Help Test TextToSpeech \
               WebSockets Sql OpenGLWidgets Lottie WebEngineQuick; do
    rm -f "$SITE/PyQt6/Qt${pattern}"*.so "$SITE/PyQt6/Qt${pattern}"*.pyi
done
for pattern in Designer Multimedia SpatialAudio 3D Quick3D QuickControls QuickDialogs QuickTemplates \
               QuickParticles QuickShapes QuickTimeline QuickVectorImage QuickEffects QuickLayouts \
               QuickTest Charts DataVis Graphs RemoteObjects Sensors SerialPort Bluetooth Nfc Help \
               Test TextToSpeech WebSockets Sql Lottie ShaderTools WebEngineQuick; do
    rm -f "$QT/lib/libQt6${pattern}"*.so*
done
# WHY: plugins of the removed parts (3D, QML tools, help, multimedia...) would point at missing libraries
rm -rf "$QT/plugins/"{sqldrivers,multimedia,qmltooling,qmlls,qmllint,sceneparsers,renderers,renderplugins,geometryloaders,assetimporters,help,scxmldatamodel,sensors,texttospeech,position,webview,egldeviceintegrations} 2>/dev/null || true
# WHY: FFmpeg libraries of Qt Multimedia (not used; WebEngine has its own decoder built in)
rm -f "$QT/lib/"{libavcodec,libavformat,libavutil,libswresample,libswscale,libQt6FFmpegStub,libQt6LabsPlatform}* 2>/dev/null || true
# WHAT: every remaining Qt library, plugin and helper must find its Qt libraries INSIDE the bundle.
# WHY:  a plain ldd check would also accept Qt libraries installed on the build machine - the
#       AppImage then fails on a computer without them (found on a clean Fedora).
MISSING=""
while IFS= read -r -d '' f; do
    for lib in $(ldd "$f" 2>/dev/null | grep -o 'libQt6[A-Za-z0-9]*\.so\.6' | sort -u); do
        [ -e "$QT/lib/$lib" ] || MISSING="$MISSING${f#"$QT"/}: $lib"$'\n'
    done
done < <(find "$QT" -type f \( -name "*.so*" -o -name "QtWebEngineProcess" \) -print0)
if [ -n "$MISSING" ]; then
    echo "Trimmed too much - Qt libraries missing from the AppImage:" >&2
    printf '%s' "$MISSING" >&2
    exit 1
fi
rm -rf "$APP_DIR/usr/python/lib/python3.12/"{test,idlelib,tkinter,turtledemo,ensurepip,lib2to3} \
       "$APP_DIR/usr/python/lib/"{libtcl,libtk}* "$APP_DIR/usr/python/lib/"{tcl,tk}[0-9]* 2>/dev/null || true
find "$APP_DIR/usr/python" -name "__pycache__" -type d -prune -exec rm -rf {} +

# WHAT: a quick check that the trimmed Python still loads everything the app imports.
QT_QPA_PLATFORM=offscreen "$PY" -s -c '
from PyQt6 import QtCore, QtGui, QtWidgets, QtSvg, QtNetwork, QtDBus, QtPdf, QtWebEngineWidgets
import requests, sqlite3, zipfile, ssl
print("Python", __import__("sys").version.split()[0], "- Qt", QtCore.QT_VERSION_STR, "- imports OK")
'

# --- 3. The app ---------------------------------------------------------------
cp lamadeck.py theme.qss logo.png "$APP_DIR/usr/share/lamadeck/"
cp -r lang fonts web vendor "$APP_DIR/usr/share/lamadeck/"
rm -rf "$APP_DIR/usr/share/lamadeck/vendor/__pycache__"  # WHY: Python cache from a local run

# WHAT: AppRun - what the AppImage runtime starts.
# WHY:  -s (no user site-packages) and -E (ignore PYTHONPATH/PYTHONHOME): a PyQt6
#       installed with "pip --user" or the user's Python settings must not mix
#       with the bundled one. Flags instead of environment variables, so nothing
#       leaks into the programs the app starts (systemctl, uv, Open WebUI...).
cat > "$APP_DIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/python/bin/python3" -s -E "$HERE/usr/share/lamadeck/lamadeck.py" "$@"
EOF
chmod 755 "$APP_DIR/AppRun"

# WHY: the .desktop file and icon in the AppDir root are required by the format;
#      the app writes its own menu entry (pointing at the .AppImage) on request.
cat > "$APP_DIR/lamadeck.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=LamaDeck
Comment=Control panel for your local AI stack (Ollama, LiteLLM, Open WebUI)
Comment[pl]=Panel sterowania lokalnym stosem AI (Ollama, LiteLLM, Open WebUI)
Exec=lamadeck
Icon=lamadeck
Categories=Utility;
Terminal=false
X-AppImage-Version=$VERSION
EOF
cp logo.png "$APP_DIR/lamadeck.png"
ln -sf lamadeck.png "$APP_DIR/.DirIcon"

# --- 4. Pack -------------------------------------------------------------------
APPIMAGETOOL="$CACHE_DIR/appimagetool-x86_64.AppImage"
if [ ! -f "$APPIMAGETOOL" ]; then
    echo "Downloading appimagetool..."
    curl -fL --progress-bar -o "$APPIMAGETOOL.part" "$APPIMAGETOOL_URL"
    mv "$APPIMAGETOOL.part" "$APPIMAGETOOL"
    chmod 755 "$APPIMAGETOOL"
fi
rm -f "$OUTPUT"
# WHY: APPIMAGE_EXTRACT_AND_RUN - works without FUSE (containers, minimal systems)
if ! ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL" --no-appstream "$APP_DIR" "$OUTPUT" \
        > "$WORK_DIR/appimagetool.log" 2>&1; then
    cat "$WORK_DIR/appimagetool.log" >&2
    exit 1
fi
echo "Done: ${OUTPUT#"$SCRIPT_DIR"/} ($(du -h "$OUTPUT" | cut -f1))"
