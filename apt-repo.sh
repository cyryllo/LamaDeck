#!/bin/bash
# =============================================================================
#  LamaDeck APT repository builder.
#
#  WHAT: builds a small, signed APT repository with the lamadeck package and
#        publishes it on GitHub Pages (the gh-pages branch of this repo), so
#        users can install and update the app with plain apt:
#            https://cyryllo.github.io/LamaDeck/apt
#  WHY:  an own repository needs nobody's approval (unlike official Debian or
#        a PPA), and it's just static files - no server to run. The .deb itself
#        also ships the repository entry and its public key (see build-deb.sh),
#        so installing one .deb from GitHub is enough to get updates via apt.
#
#  Commands:
#    ./apt-repo.sh init-key         create the signing key - ONCE, asks for a passphrase
#    ./apt-repo.sh build [x.deb..]  (re)build apt-repo/ - by default from a fresh build-deb.sh
#    ./apt-repo.sh publish          push the project page (site/) and apt-repo/ to the
#                                   gh-pages branch (GitHub Pages)
#    ./apt-repo.sh preview          assemble the page locally (build/pages) to look at it
#
#  Environment:
#    LAMADECK_KEY   key id/fingerprint to sign with (default: the key whose name is
#                   "LamaDeck APT repository")
#    GNUPGHOME      as usual for gpg (used by the tests with a throwaway key)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
cd "$SCRIPT_DIR"

REPO_DIR="$SCRIPT_DIR/apt-repo"            # WHY: kept between builds (gitignored) - older versions stay in the pool
REPO_URL="https://cyryllo.github.io/LamaDeck/apt"
KEY_NAME="LamaDeck APT repository"
# WHY: the public key also lives in the source tree - build-deb.sh puts it into the package,
#      so apt trusts the repository right after the first .deb install
PUBLIC_KEY_FILE="$SCRIPT_DIR/packaging/lamadeck-archive-keyring.gpg"
KEEP_VERSIONS=3                            # WHY: apt only needs the newest; a few old ones allow a downgrade

_key_id() {
    # WHAT: fingerprint of the signing key (LAMADECK_KEY or found by its name).
    if [ -n "${LAMADECK_KEY:-}" ]; then
        echo "$LAMADECK_KEY"
        return
    fi
    gpg --list-secret-keys --with-colons "$KEY_NAME" 2>/dev/null | awk -F: '/^fpr:/ {print $10; exit}'
}

init_key() {
    # WHAT: creates the repository signing key and exports its public part.
    # WHY:  RSA 4096 - accepted by every apt still in use (Ed25519 needs newer gpgv);
    #       signing only; no expiry, so old installs keep updating. gpg asks for the
    #       passphrase in its own dialog - the private key never leaves ~/.gnupg.
    if [ -n "$(_key_id)" ]; then
        echo "The key \"$KEY_NAME\" already exists - nothing to do."
    else
        local email
        email="$(git config user.email || true)"
        gpg --quick-generate-key "$KEY_NAME${email:+ <$email>}" rsa4096 sign never
    fi
    mkdir -p "$(dirname "$PUBLIC_KEY_FILE")"
    gpg --export "$(_key_id)" > "$PUBLIC_KEY_FILE"
    echo "Public key saved to ${PUBLIC_KEY_FILE#"$SCRIPT_DIR"/} - commit it (it's public, needed by build-deb.sh)."
    echo "IMPORTANT: back up the private key, without it you can't publish updates:"
    echo "  gpg --export-secret-keys --armor \"$(_key_id)\" > lamadeck-signing-key-BACKUP.asc"
}

build_repo() {
    local key
    key="$(_key_id)"
    if [ -z "$key" ]; then
        echo "No signing key - run './apt-repo.sh init-key' first." >&2
        exit 1
    fi
    local debs=("$@")
    if [ ${#debs[@]} -eq 0 ]; then
        ./build-deb.sh
        debs=("$(ls -t lamadeck_*_all.deb | head -n1)")
    fi

    mkdir -p "$REPO_DIR/pool/main"
    cp "${debs[@]}" "$REPO_DIR/pool/main/"
    # WHY: only the newest KEEP_VERSIONS packages stay - the repo shouldn't grow forever
    ls "$REPO_DIR"/pool/main/lamadeck_*_all.deb | sort -V | head -n -"$KEEP_VERSIONS" | xargs -r rm -f

    cd "$REPO_DIR"
    rm -rf dists
    # WHY: the package is "Architecture: all", but apt on amd64/arm64 reads binary-<arch>,
    #      so the same Packages list goes to each (plus binary-all for newer apt)
    for arch in amd64 arm64 all; do
        mkdir -p "dists/stable/main/binary-$arch"
        dpkg-scanpackages --multiversion pool /dev/null 2>/dev/null > "dists/stable/main/binary-$arch/Packages"
        gzip -9 -k -f "dists/stable/main/binary-$arch/Packages"
    done
    apt-ftparchive \
        -o APT::FTPArchive::Release::Origin="LamaDeck" \
        -o APT::FTPArchive::Release::Label="LamaDeck" \
        -o APT::FTPArchive::Release::Suite="stable" \
        -o APT::FTPArchive::Release::Codename="stable" \
        -o APT::FTPArchive::Release::Architectures="amd64 arm64 all" \
        -o APT::FTPArchive::Release::Components="main" \
        -o APT::FTPArchive::Release::Description="LamaDeck - control panel for a local AI stack" \
        release dists/stable > dists/stable/Release
    # WHY: InRelease (inline signature) is what modern apt fetches; Release.gpg for older ones
    gpg --batch --yes --local-user "$key" --clearsign -o dists/stable/InRelease dists/stable/Release
    gpg --batch --yes --local-user "$key" --armor --detach-sign -o dists/stable/Release.gpg dists/stable/Release

    # WHAT: the public key and a ready-made source entry, for adding the repo by hand
    gpg --export "$key" > lamadeck-archive-keyring.gpg
    cat > lamadeck.sources <<EOF
Types: deb
URIs: $REPO_URL
Suites: stable
Components: main
Signed-By: /usr/share/keyrings/lamadeck-archive-keyring.gpg
EOF
    _index_page > index.html
    touch .nojekyll   # WHY: GitHub Pages must serve the files as they are, without Jekyll
    cd "$SCRIPT_DIR"
    echo "Repository ready in ${REPO_DIR#"$SCRIPT_DIR"/}/ - packages:"
    ls "$REPO_DIR/pool/main"
}

_index_page() {
    # WHAT: a short instruction page for people who open the repository address in a browser.
    cat <<EOF
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>LamaDeck APT repository</title>
<style>body{font-family:sans-serif;max-width:46em;margin:2em auto;padding:0 1em;line-height:1.5}
pre{background:#f2f2f2;padding:.8em;overflow-x:auto}</style></head><body>
<h1>LamaDeck APT repository</h1>
<p>Debian / Ubuntu repository of <a href="https://github.com/cyryllo/LamaDeck">LamaDeck</a>.
Installing the <code>.deb</code> from the GitHub releases adds it automatically.
To add it by hand:</p>
<pre>sudo curl -fsSLo /usr/share/keyrings/lamadeck-archive-keyring.gpg $REPO_URL/lamadeck-archive-keyring.gpg
sudo curl -fsSLo /etc/apt/sources.list.d/lamadeck.sources $REPO_URL/lamadeck.sources
sudo apt update
sudo apt install lamadeck</pre>
</body></html>
EOF
}

_assemble_pages() {
    # WHAT: puts the whole GitHub Pages site into $1: the project page from site/, the
    #       screenshots and logo (img/), the fonts with their licences and the APT repo (apt/).
    # WHY:  pictures and fonts are copied from where the app and README already keep them,
    #       so there is only one copy of each in the source tree
    local out="$1"
    cp -r site/. "$out/"
    mkdir -p "$out/img" "$out/fonts"
    cp screenshots/*.png logo.png "$out/img/"
    cp fonts/Inter-Regular.ttf fonts/Inter-SemiBold.ttf fonts/Inter-Bold.ttf \
       fonts/JetBrainsMono-Regular.ttf fonts/*-LICENSE.txt "$out/fonts/"
    if [ -d "$REPO_DIR" ]; then
        mkdir -p "$out/apt"
        cp -r "$REPO_DIR"/. "$out/apt/"
    fi
    touch "$out/.nojekyll"   # WHY: GitHub Pages must serve the files as they are, without Jekyll
}

preview_pages() {
    rm -rf build/pages
    mkdir -p build/pages
    _assemble_pages build/pages
    echo "Page ready: file://$SCRIPT_DIR/build/pages/index.html"
}

publish_repo() {
    # WHAT: replaces the gh-pages branch content with the assembled site and pushes it.
    # WHY:  a separate git worktree - the working copy of main isn't touched at all.
    if [ ! -f "$REPO_DIR/dists/stable/InRelease" ]; then
        echo "Nothing to publish - run './apt-repo.sh build' first." >&2
        exit 1
    fi
    local worktree
    worktree="$(mktemp -d)"
    # WHY: double quotes - the path is put in now; at exit the local variable is already gone
    #      (with single quotes the worktree stayed behind in /tmp)
    # shellcheck disable=SC2064
    trap "git worktree remove --force '$worktree' >/dev/null 2>&1 || true; git branch -D gh-pages-new >/dev/null 2>&1 || true" EXIT
    if git ls-remote --exit-code --heads origin gh-pages >/dev/null 2>&1; then
        git fetch origin gh-pages
        git worktree add "$worktree" origin/gh-pages --detach
    else
        git worktree add --detach "$worktree"
        git -C "$worktree" checkout --orphan gh-pages-new
        git -C "$worktree" rm -rf --quiet . >/dev/null 2>&1 || true
    fi
    # WHY: the branch is generated - start from empty, so removed files go away too
    git -C "$worktree" rm -rf --quiet . >/dev/null 2>&1 || true
    _assemble_pages "$worktree"
    git -C "$worktree" add -A
    if git -C "$worktree" diff --cached --quiet; then
        echo "The published repository is already up to date."
        return
    fi
    git -C "$worktree" commit -q -m "APT repository: $(ls "$REPO_DIR"/pool/main | sort -V | tail -n1)"
    git -C "$worktree" push origin HEAD:refs/heads/gh-pages
    echo "Published: https://cyryllo.github.io/LamaDeck/ (APT: $REPO_URL)"
    echo "First time? Enable GitHub Pages: repository Settings -> Pages -> Branch: gh-pages, folder / (root)."
}

case "${1:-}" in
    init-key) init_key ;;
    build) shift; build_repo "$@" ;;
    publish) publish_repo ;;
    preview) preview_pages ;;
    *)
        echo "Usage: $0 init-key | build [package.deb ...] | publish | preview" >&2
        exit 1
        ;;
esac
