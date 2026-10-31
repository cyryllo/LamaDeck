#!/bin/bash
# =============================================================================
#  LamaDeck - test the packages in clean distributions (Docker).
#
#  WHAT: keeps ready-made test images (Docker) and runs the app in them:
#          fedora  - the AppImage (Fedora with the usual desktop libraries)
#          debian  - the .deb on Debian 13
#          ubuntu  - the .deb on Ubuntu 24.04
#  WHY:  the images are built once and stay in Docker, so a test starts in
#        seconds; every run uses a fresh container (nothing left from the last test).
#
#  Usage:
#    ./test-in-docker.sh fedora|debian|ubuntu          smoke test (offscreen, 15 s)
#    ./test-in-docker.sh fedora|debian|ubuntu gui      open the real window on your desktop
#    ./test-in-docker.sh fedora|debian|ubuntu shell    shell in the container (repo in /src)
#    ./test-in-docker.sh rebuild fedora|debian|ubuntu  build the image again (e.g. updates)
#
#  Uses the newest LamaDeck-*-x86_64.AppImage / lamadeck_*_all.deb from this folder
#  (build them with ./build-appimage.sh / ./build-deb.sh).
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
cd "$SCRIPT_DIR"

_image_name() { echo "lamadeck-test-$1"; }

_build_image() {
    # WHAT: builds the test image for one distribution (only the system part, not the app).
    local distro="$1" dockerfile
    case "$distro" in
        fedora)
            # WHY: a desktop Fedora always has these (GL, X11/Wayland, fonts, systemd,
            #      polkit) - the bare container image doesn't
            dockerfile='FROM fedora:latest
RUN dnf install -y mesa-libGL mesa-libEGL libxkbcommon libxkbcommon-x11 fontconfig \
    dejavu-sans-fonts freetype dbus-libs libX11 libX11-xcb libxcb xcb-util-cursor \
    xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm xcb-util \
    libwayland-client libwayland-cursor libwayland-egl glib2 libICE libSM systemd polkit \
    && dnf clean all'
            ;;
        debian|ubuntu)
            local base="debian:trixie"
            [ "$distro" = ubuntu ] && base="ubuntu:24.04"
            # WHY: the dependencies of the .deb pre-installed, so a test only installs the
            #      package itself; libgl1/fonts for the real window in "gui" mode
            dockerfile="FROM $base
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pyqt6 \
    python3-pyqt6.qtsvg python3-pyqt6.qtpdf python3-requests pkexec systemd curl \
    ca-certificates libgl1 libegl1 fonts-dejavu-core qt6-wayland && rm -rf /var/lib/apt/lists/*"
            ;;
        *)
            echo "Unknown distribution: $distro (fedora, debian or ubuntu)" >&2
            exit 1
            ;;
    esac
    echo "Building the test image $(_image_name "$distro")..."
    printf '%s\n' "$dockerfile" | docker build -q -t "$(_image_name "$distro")" - >/dev/null
}

_ensure_image() {
    docker image inspect "$(_image_name "$1")" >/dev/null 2>&1 || _build_image "$1"
}

_newest() {
    # WHAT: the newest file matching the pattern (by version number).
    ls $1 2>/dev/null | sort -V | tail -n1
}

_run() {
    local distro="$1" mode="${2:-test}" package install_cmd run_cmd
    _ensure_image "$distro"
    if [ "$distro" = fedora ]; then
        package="$(_newest 'LamaDeck-*-x86_64.AppImage')"
        [ -n "$package" ] || { echo "No AppImage - run ./build-appimage.sh first." >&2; exit 1; }
        install_cmd="cp /src/$package /tmp/LamaDeck.AppImage"
        # WHY: no FUSE in a container - the AppImage runs extracted
        run_cmd="env APPIMAGE_EXTRACT_AND_RUN=1 /tmp/LamaDeck.AppImage"
    else
        package="$(_newest 'lamadeck_*_all.deb')"
        [ -n "$package" ] || { echo "No .deb - run ./build-deb.sh first." >&2; exit 1; }
        install_cmd="apt-get install -y -qq /src/$package >/dev/null 2>&1"
        run_cmd="lamadeck"
    fi
    echo "== $distro: $package"

    local args=(--rm -v "$SCRIPT_DIR:/src:ro" -e HOME=/tmp/home)
    case "$mode" in
        test)
            docker run "${args[@]}" "$(_image_name "$distro")" bash -c "
                mkdir -p /tmp/home && $install_cmd
                QT_QPA_PLATFORM=offscreen timeout 15 $run_cmd > /tmp/log 2>&1; code=\$?
                grep -v 'propagateSizeHints\|XDG_RUNTIME_DIR' /tmp/log | head -20
                if [ \$code -eq 124 ]; then echo 'OK - the app ran for 15 s without errors'; else echo \"FAILED - exit code \$code\"; exit 1; fi"
            ;;
        gui)
            # WHY: the window is shown on your own desktop through the Wayland socket
            #      (X11 as a fallback); the app's data stays in the container and is gone after
            if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -n "${XDG_RUNTIME_DIR:-}" ]; then
                args+=(-v "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY:/tmp/rt/$WAYLAND_DISPLAY"
                       -e XDG_RUNTIME_DIR=/tmp/rt -e WAYLAND_DISPLAY="$WAYLAND_DISPLAY" -e QT_QPA_PLATFORM=wayland)
            elif [ -n "${DISPLAY:-}" ]; then
                args+=(-v /tmp/.X11-unix:/tmp/.X11-unix -e DISPLAY="$DISPLAY" -e QT_QPA_PLATFORM=xcb)
            else
                echo "No desktop session found (WAYLAND_DISPLAY / DISPLAY)." >&2
                exit 1
            fi
            docker run "${args[@]}" "$(_image_name "$distro")" bash -c "
                mkdir -p /tmp/home && chmod 700 /tmp/rt 2>/dev/null; $install_cmd && $run_cmd" \
                || true
            ;;
        shell)
            docker run -it "${args[@]}" "$(_image_name "$distro")" bash -c "mkdir -p /tmp/home; cd /src; echo 'Install: $install_cmd'; echo 'Run:     $run_cmd'; exec bash"
            ;;
        *)
            echo "Unknown mode: $mode (gui or shell)" >&2
            exit 1
            ;;
    esac
}

case "${1:-}" in
    fedora|debian|ubuntu) _run "$1" "${2:-test}" ;;
    rebuild) _build_image "${2:?which distribution: fedora, debian or ubuntu}" ;;
    *)
        echo "Usage: $0 fedora|debian|ubuntu [gui|shell]  |  $0 rebuild fedora|debian|ubuntu" >&2
        exit 1
        ;;
esac
