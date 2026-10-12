# Ollama Manager

A desktop app (PyQt6) for KDE that started as a simple [Ollama](https://ollama.com) service
manager and grew into a control panel for a whole local AI stack: multi-host model management,
a model aggregator (LiteLLM) that exposes one OpenAI-compatible endpoint for tools like
Continue/VS Code, and a browser chat UI (Open WebUI). No terminal, no Docker, no cloud —
everything runs on your LAN.

*(Polska wersja tego pliku: [README_PL.md](README_PL.md))*

> **Status: stable (v0.5.0).** Daily driver on my own setup, working as expected. Still
> actively developed — new options and features keep coming; [bug reports and
> feedback](../../issues) are welcome.

![Ollama Manager — Models tab](om.png)

## Requirements

- Python 3 + **PyQt6**, **requests**
- **systemd** + **polkit** (`pkexec`) — standard on KDE/Debian; `install.sh` offers to install
  `pkexec` via `apt` if it's missing (needed for every action requiring admin rights)
- **curl** — used by the app's own install buttons (Ollama, and uv for Open WebUI/LiteLLM);
  `install.sh` offers to install it via `apt` if it's missing
- Ollama (if you don't have it, the app installs it with one click)
- Optional: **uv** (for installing Open WebUI and LiteLLM — the app installs it itself if needed)
- Optional: `ffmpeg`, `pandoc`, `zstd` (full Open WebUI functionality — voice, document RAG;
  the app offers to install these via `apt` right after a successful Open WebUI install)

## Installation and running

First, clone the repository and enter its directory — the install scripts below
expect to be run from there (they look for `ollama_manager.py` next to themselves):

```
git clone https://github.com/cyryllo/Ollama-manager.git
cd Ollama-manager
```

**As a menu app, no root (recommended)** — installs dependencies via pip, copies the app to
`~/.local/share/ollama-manager` and adds a menu entry (Utilities section). Detects an existing
install and offers to update/reinstall accordingly:

```
./install.sh
```

Uninstall with `./install.sh --uninstall`.

**As a `.deb` package (Debian/Ubuntu)** — dependencies come from `apt`, easy to uninstall:

```
./build-deb.sh
sudo apt install ./ollama-manager_*_all.deb
```

**Manually, for development** — no copying, no menu entry:

```
pip install PyQt6 requests
python3 ollama_manager.py
```

## Features

**Ollama service**
- Start / stop the systemd service, live status detection
- Autostart on system boot
- Detects a missing install + a one-click install button
- On-demand update check against the latest GitHub release, with a prompt to reinstall
  (the official install script updates an existing install in place) if you're behind — useful
  since newer models sometimes need a newer Ollama to even load

**Models**
- **One search for two sources** — type a model name and the app searches the official Ollama
  library (ollama.com) and Hugging Face (GGUF models only) at once. Every result has a **Source**
  column, so it's always clear where a model comes from. Pick a result to see all its variants
  (tags/quantizations) with their **real file sizes**, pick a variant, click Download — with a
  progress bar. You never need to know the exact tag up front. MLX variants (Apple-only) and
  "cloud" models (not downloadable) are skipped
- Pasting a Hugging Face link (or `user/repo`) jumps straight to that repo's variants; typing an
  exact name (e.g. `llama3.2:3b` or `hf.co/...:Q4_K_M`) lets you download without searching
- **Hugging Face is an optional, community source** — anyone can publish there and the app doesn't
  vet repo contents, so a note in the card recommends sticking to well-known quantization
  publishers (e.g. bartowski, mradermacher, unsloth)
- Approximate memory-usage estimate for the chosen variant (real file size + KV cache + buffers,
  then a recommended-RAM figure; details in a tooltip)
- Table of installed models — name, **source** (Ollama / Hugging Face), disk size and an
  estimated/live VRAM column — + deletion. Hover a row for its source (with the quantization
  author for HF), model family, parameter size, quantization and download date
- Preview of models currently loaded into memory (VRAM)
- Note: ollama.com has no public search API, so the app reads its search/tags web pages — if the
  site's layout changes, Ollama search may need a fix (Hugging Face uses a real API) — the app then
  shows an error instead of silently listing only Hugging Face results. Ollama results are always
  listed first; cloud-only Ollama models (not downloadable) are skipped

**Open WebUI**
- One-click install of the browser chat panel (no Docker)
- Start / stop, autostart on login
- The "Open WebUI" button opens the panel in the browser (no automatic opening)
- On-demand update check against the latest PyPI release, with a prompt to upgrade via
  `uv tool upgrade` (no admin rights needed) if you're behind
- After a fresh install, offers to install the optional `ffmpeg`/`pandoc`/`zstd` packages
  needed for voice transcription and document RAG (the only step that needs admin rights)

**Server switcher**
- Choose the Ollama host (localhost or any host on the LAN, e.g. BC-250) for model operations
- Add/remove servers from the window, remembered between runs

**Model aggregator (LiteLLM)**
- One-click install, start/stop and autostart for LiteLLM (no Docker)
- Exposes a single endpoint (OpenAI-compatible) combining models from ALL
  servers on the switcher list — VS Code/Continue only needs to point at this one address
- Preview of which models and hosts will end up in the config, before starting
- Optional **API key (password)** for the aggregator — off by default, so anyone on your network can
  use the models; turn it on and the app generates a random key once, shows it with a copy button
  and puts it into the Continue config and the Unsloth Studio details automatically
- Generates a ready-to-paste Continue.dev `config.yaml` for the exposed models — copy/paste only,
  the app never writes to your files
- Connection details for **Unsloth Studio** — Studio has no config file (connections are added in
  its Settings → Connections), so the app shows exactly what to enter (connection type, Base URL,
  API key) plus the list of model IDs to copy
- On-demand update check against the latest PyPI release, with a prompt to upgrade via
  `uv tool upgrade` (no admin rights needed) if you're behind

**Advanced (Ollama environment variables)**
- `OLLAMA_KEEP_ALIVE`, `OLLAMA_CONTEXT_LENGTH` (default 4096 is too small for agentic work),
  `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_NUM_PARALLEL`, `OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`,
  `OLLAMA_VULKAN` (Vulkan backend instead of ROCm, useful on AMD cards without full ROCm support
  e.g. BC-250), `OLLAMA_IGPU_ENABLE`, `OLLAMA_HOST` (listen on the LAN instead of localhost-only),
  `GGML_VK_VISIBLE_DEVICES` (Vulkan device index — needed on some APUs), `OLLAMA_GPU_OVERHEAD`
  (VRAM reserved for the rest of the system — desktop/server preset picker)
- A single "Save" applies every field at once and restarts the service once, plus an "Apply
  recommended values" button that pre-fills a sane starting profile for review before saving
- A free-form field for any other environment variable not covered by the form above
- Full descriptions of each variable live in a separate "Help" tab, keeping this tab compact

**Stats bar**
- Ollama and Open WebUI status
- VRAM usage on the currently selected server
- **RAM used by Ollama** and the **VRAM + RAM** total — the real memory use. Ollama keeps part of a
  model in RAM (it maps model files into memory, especially when a model doesn't fit in VRAM). For
  the local Ollama it's measured from its processes (process memory + mapped model files, details
  in a tooltip); for a remote server it's the part of loaded models outside VRAM, from its API.
  Loaded models also show their split, e.g. `20.0 GB VRAM + 16.0 GB RAM`
- Number of installed models

**Event log** — a log of all operations, always visible at the bottom of the window.

**Interface language** — Polish, English, German, Spanish, French, Portuguese and Italian, switchable from the window, remembered between runs.

## Notes

- Ollama service control always targets the local machine — even if a remote
  server (e.g. BC-250) is selected in the window, start/stop/autostart act locally.
