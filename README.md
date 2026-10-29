# Ollama Manager

A desktop app (PyQt6) for KDE that started as a simple [Ollama](https://ollama.com) service
manager and grew into a control panel for a whole local AI stack: multi-host model management,
a model aggregator (LiteLLM) that exposes one OpenAI-compatible endpoint for tools like
Continue/VS Code, and a browser chat UI (Open WebUI). No terminal, no Docker, no cloud —
everything runs on your LAN.

*(Polska wersja tego pliku: [README_PL.md](README_PL.md))*

> **Status: stable (v0.6.0).** Daily driver on my own setup, working as expected. Still
> actively developed — new options and features keep coming; [bug reports and
> feedback](../../issues) are welcome.

![Ollama Manager — Dashboard](om.png)

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

The window has a sidebar with seven screens, in a dark Breeze look (Inter + JetBrains Mono
fonts are bundled, so it looks the same everywhere).

**Dashboard**
- One switch for all services (Ollama, LiteLLM router, Open WebUI) plus "Restart" and "Stop all"
- A card per service with its status and its own on/off switch — or an "Install" button if it's
  missing (Ollama via its official script, the others via `uv`, no Docker, no admin rights)
- Ollama card: which hosts are online, which models are loaded in memory, live GPU load
- Router card: the routes (alias → model @ hosts) at a glance
- Open WebUI card: one big button — install / start / open in the browser
- Computer resources: CPU, VRAM and RAM gauges, network and CPU charts

**Ollama hosts**
- All Ollama hosts (this computer and any on the LAN, e.g. BC-250): online/offline, Ollama
  version, response time, VRAM and RAM used by models (with the totals for this computer) and the
  models on each host — on disk or loaded into memory
- Add a host by just typing its address (`http://` and port 11434 are added for you), remove it
  with one click; the list is shared by the router and the Models screen
- For this computer: restart the Ollama service and jump to its log
- A host that doesn't respond gets a hint on what to check (`OLLAMA_HOST=0.0.0.0`)

**LiteLLM router**
- One OpenAI-compatible address (`http://localhost:4000/v1`) for all programs, combining the
  models of every host
- A live route diagram: programs → LiteLLM → aliases → hosts
- **Aliases** — fixed names such as `chat` or `code` for a model on chosen hosts; programs use the
  alias, so you can swap the model behind it without touching them
- What to do when a model is on several hosts: take turns, least busy, or fastest
- Choose which models the router offers under their own names (checkbox per model and host)
- Optional **API key** — off by default (anyone on your network can use the models)
- Ready-made settings for **Continue.dev** (a `config.yaml` to copy) and step-by-step instructions
  for connecting **Open WebUI**
- Changes wait for "Restart the router" — no surprise downtime

**Models**
- **One search for two sources** — the official Ollama library and Hugging Face (GGUF only);
  results unfold under the field with every variant and its **real file size**, plus a memory
  estimate. Type an exact name (`llama3.2:3b`, `hf.co/...:Q4_K_M`) and press Enter to download
  right away; pick the target host
- **"Only MoE models"** checkbox — shows only mixture-of-experts models (and only their MoE
  variants), which run much faster on APUs and computers without a strong graphics card.
  Detected by description, name, Hugging Face tags and Ollama variant tags (e.g. `30b-a3b`)
- Download progress with size and speed, and a "Cancel" button (resumes later where it stopped)
- Tiles for the models on **all hosts**, filterable by kind (chat, code, embedding, vision): size,
  quantization, context length and on which hosts the model is (green = loaded in memory)
- "Load" / "Unload" a model to/from memory, "Copy to host" (downloads the same model on another
  host), delete from one or all hosts
- Hugging Face is a community source — the app suggests well-known quantization authors

**Open WebUI**
- Start, restart, stop, open in the browser, copy the address
- Settings: open the browser after start, require login (only switchable off while there are no
  accounts — otherwise Open WebUI would lock existing users out), port
- Installation details: environment, where chats are stored, whether it's connected to the router;
  **one-click backup** of all chat data (a ZIP file)
- After a fresh install, offers the optional `ffmpeg`/`pandoc`/`zstd` packages (voice, document
  RAG — the only step that needs admin rights)

**Log**
- Messages from Ollama, LiteLLM, Open WebUI and the app itself in one place, filterable per service
- Errors come with a **plain explanation** of what they mean and, where it applies, a "Show host"
  link
- Save to a file, clear the view, auto-scroll
- The Ollama service log needs permission: load it once (password) or allow it permanently

**System tray** (optional, off by default — turn it on in Settings)
- A tray icon with a small panel: the three services with on/off switches, CPU / VRAM / RAM,
  models in memory, and buttons to open the chat or the control panel
- Closing the window hides it in the tray; quit from the icon's menu
- Optionally start the program after login, already hidden in the tray
- Only one copy runs at a time — starting it again just brings the window back

**Settings**
- Interface language — Polish, English, German, Spanish, French, Portuguese, Italian; switches
  instantly, remembered between runs
- Autostart of all three services in one place: Ollama with the system, the router and Open WebUI
  after login
- Versions and on-demand update checks for Ollama, LiteLLM and Open WebUI
- Ollama environment variables, each with a short description: `OLLAMA_KEEP_ALIVE`,
  `OLLAMA_CONTEXT_LENGTH` (default 4096 is too small for agentic work), `OLLAMA_MAX_LOADED_MODELS`,
  `OLLAMA_NUM_PARALLEL`, `OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`, `OLLAMA_VULKAN`,
  `OLLAMA_IGPU_ENABLE`, `OLLAMA_HOST`, `IPAddressAllow` (limit LAN access),
  `GGML_VK_VISIBLE_DEVICES`, `OLLAMA_GPU_OVERHEAD`, plus free-form extra variables — one "Save"
  applies everything with a single service restart

## Notes

- Ollama service control always targets the local machine — even if a remote
  server (e.g. BC-250) is selected in the window, start/stop/autostart act locally.
