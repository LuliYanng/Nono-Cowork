English | [简体中文](README_zh-CN.md)

<h1 align="center">Nono CoWork</h1>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.12+-3776AB.svg?logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="https://github.com/KilYep/nono-cowork/stargazers"><img src="https://img.shields.io/github/stars/KilYep/nono-cowork?style=social" alt="GitHub stars"></a>
  <a href="https://github.com/KilYep/nono-cowork/commits/main"><img src="https://img.shields.io/github/last-commit/KilYep/nono-cowork" alt="Last Commit"></a>
</p>

<h3 align="center">The proactive agent for real workflows — not just browser tasks.</h3>

<p align="center">A background coworker that runs on your VPS, watches for events, gets work done,<br>and syncs the results back to your local workspace.</p>

Most AI agents wait for a prompt. Nono starts when something happens.

It can monitor your email, synced folders, and the apps you connect to it. When a partner sends a contract at 2 AM, Nono downloads the attachment, retrieves last year's agreement from your synced workspace, compares them clause by clause, flags key changes, and drafts a reply.

By the time you open your laptop in the morning, a notification card is waiting on your desktop: *"Contract received. 3 key changes flagged. Draft reply ready for review."* The diff report is already in your local folder. No downloads, no separate dashboard — **the file is already where you work.** Click "Send", and the email goes out.

Away from your computer? Nono can notify you via Telegram or Feishu too.

**This isn't an assistant waiting for instructions. It's a coworker that's already at work.**

<p align="center">
  <video src="https://github.com/user-attachments/assets/966b3346-12aa-4989-b041-d2571fd64fd5" width="800" controls autoplay loop muted></video>
</p>

---

## What Makes This Different

AI agents can already do a lot. But most still fall into the same trade-offs:

| Approach | The Problem |
|:---|:---|
| **Cloud agents** | Work 24/7, but files stay in their cloud. You still have to download and move everything back into your workflow. |
| **Desktop agents** | Can work with local files, but usually require your computer to stay online — and often need broader access to your local environment. |
| **Automation tools** | Great at connecting apps, but limited to predefined if-this-then-that workflows. |

**Nono CoWork takes a different approach: it keeps the agent online on your VPS while delivering outputs back into the folders you already use.**

- 🧠 **Proactive** — Monitors email, file changes, and connected apps. Acts when something important happens — no prompt required.
- ☁️ **Always on** — Runs continuously on your VPS, so work can keep moving even when your laptop is closed.
- 📁 **Local-first delivery** — Results sync directly into your local folders, so outputs show up where you already work.
- 🔒 **Isolated by architecture** — Runs on your VPS and cannot directly control your local device. It only sees the folders you explicitly sync.
- ✋ **Human-in-the-loop** — Drafts the email, but waits for your approval before sending. Critical actions wait for your review.

---

## It Moves Your Workflow Forward

| When this happens | Nono gets this done first | You only need to... |
| :--- | :--- | :--- |
| 📧 A partner sends a new contract | Download the attachment → retrieve related versions → compare key clauses → draft a reply | Review the diff and decide whether to send |
| 📬 A client goes silent for 3 days | Detect the stalled thread → quote the original conversation → draft a polite follow-up email | Click confirm and let it send |
| 📊 You drop a spreadsheet into your local work folder | Detect the new file → run analysis → generate charts and conclusions → save a finished report | Open the report |
| 🗂️ Your inbox fills up with PDFs, screenshots, and loose documents | Identify each file type → rename and categorize it → move it into the right folder | Check the results when you want |

> It doesn't wait for one-off prompts. When something happens, it pushes the work forward until only the final decision needs your input.

---

## Architecture

```text
  Events (24/7)                        Your VPS
  ┌──────────────┐               ┌──────────────────────────────┐
  │ 📨 Gmail      │──Composio───►│                              │
  │ 📋 GitHub     │──WebSocket──►│   Event Router               │
  │ 📅 Calendar   │──Triggers───►│      ↓                       │
  │ 📁 File Drop  │──Syncthing──►│   Agent Engine (LLM)         │
  └──────────────┘               │      ↓                       │
                                 │   Autonomous Execution       │
  Control (anytime)              │   ├─ Read/write/edit files   │
  ┌──────────────┐               │   ├─ Run shell commands      │
  │ 📱 Telegram   │─────────────►│   ├─ Search the web          │
  │ 📱 Feishu     │─────────────►│   ├─ Call 1,000+ app APIs    │
  │ 🖥️ Desktop    │──HTTP+SSE───►│   └─ Schedule future tasks   │
  │ 💻 Terminal   │─────────────►│      ↓                       │
  └──────────────┘               │   Notification System        │
                                 │   (Human-in-the-loop cards)  │
  Your devices                   │      ↓                       │
  ┌──────────────┐               │   📁 ~/Sync (workspace)      │
  │ 📁 ~/Sync    │◄──Syncthing──►│      ↕ bidirectional         │
  │ (your files) │  encrypted P2P│                              │
  └──────────────┘               └──────────────────────────────┘
```

---

## Quick Start

**Requirements:** A Linux VPS · Python ≥ 3.12 · [uv](https://docs.astral.sh/uv/) · [OpenRouter API key](https://openrouter.ai/)

```bash
# Install uv (if not installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

git clone https://github.com/KilYep/nono-cowork.git
cd nono-cowork
uv sync
cp .env.example .env   # Fill in your OPENROUTER_API_KEY
```

```bash
# Start with selected channels (recommended)
CHANNELS=desktop,feishu,telegram uv run python src/main.py

# Or run a single channel for testing
uv run agent            # Terminal REPL (simplest)
uv run feishu-bot       # Feishu only
uv run telegram-bot     # Telegram only
uv run desktop-agent    # Desktop API only
```

For long-running deployment, install the included systemd service:

```bash
# Edit nono-cowork.service first: replace YOUR_USERNAME with your actual username
sudo cp nono-cowork.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nono-cowork
```

> 💡 One API key, all models. [OpenRouter](https://openrouter.ai/) routes to Claude, GPT, Gemini, DeepSeek, Qwen, and more — switch models with one line in `.env`.

> **Minimal test (no Syncthing or Composio required):** Set `OPENROUTER_API_KEY` in `.env` and run `uv run agent`. You'll have a working agent in the terminal in under 2 minutes. Add Syncthing for file sync and Composio for app triggers when you're ready.

### Firewall / Ports

If your VPS uses a firewall, open the ports for the channels you plan to use:

```bash
sudo ufw allow 8080/tcp    # Desktop API (required for desktop app)
sudo ufw allow 22000/tcp   # Syncthing file sync
sudo ufw allow 21027/udp   # Syncthing discovery
sudo ufw allow 9090/tcp    # Composio webhook (only if using event triggers)
```

---

## Setup Guides

| Component | Guide |
|:---|:---|
| Desktop App | [docs/desktop_setup.md](docs/desktop_setup.md) |
| Syncthing File Sync | [docs/syncthing_setup.md](docs/syncthing_setup.md) |
| Telegram Bot | [docs/telegram_setup.md](docs/telegram_setup.md) |
| Feishu (Lark) Bot | [docs/feishu_setup.md](docs/feishu_setup.md) |
| Composio (App Integrations) | [docs/composio_setup.md](docs/composio_setup.md) |
| Firewall / Ports | See [Quick Start](#quick-start) above |

---

## Core Capabilities

### 🔥 Proactive Automation (Routines)
- **Cron schedules** — "Every morning at 8 AM, compile a news briefing"
- **Event triggers** — "When a new email arrives from @partner.com, process it"
- **File watchers** — "When a file appears in /inbox/, analyze it"
- **Human-in-the-loop** — Structured notification cards with approve/reject actions

### 🛠️ Agent Toolkit
- **File operations** — Read, write, and edit common file types, including PDF, Excel, and Word
- **Shell execution** — Run shell commands on the VPS
- **Web access** — Search the web and extract content from web pages
- **1,000+ app integrations** — Gmail, GitHub, Slack, Notion, and more via [Composio](https://composio.dev)
- **Syncthing control** — Check sync status, pause/resume, and restore file versions
- **Sub-agent delegation** — Spin up isolated agent sessions for complex tasks
- **Persistent context** — Remembers your preferences and context across sessions

### 📡 Multi-Channel
- **Desktop App** — Electron-based UI with real-time streaming, a notification center, routine management, built-in settings, and guided Syncthing pairing
- **Telegram** — Full-featured bot with inline actions
- **Feishu (Lark)** — Native WebSocket integration
- **Terminal** — Direct CLI access

### 🔒 Security by Architecture
- Agent runs on an **isolated VPS** — it cannot directly control your local device
- Files sync via **Syncthing's encrypted peer-to-peer protocol** — no central storage service
- **Selective sync** — the agent only sees folders you explicitly share
- **Access control** — restrict to specific Telegram/Feishu user IDs
- **API token auth** — Desktop API secured with Bearer tokens

---

## Project Status

| Area | Status |
|:---|:---|
| Terminal / Desktop / Telegram / Feishu channels | ✅ Implemented |
| Cron scheduling & event triggers | ✅ Implemented |
| Syncthing file sync & delivery | ✅ Implemented |
| Composio app integrations | ✅ Implemented (depends on Composio upstream) |
| Human-in-the-loop approval flow | ✅ Implemented |

> **Current stage: Early Beta** — Best suited to personal workflows such as document processing, email monitoring, and file automation. Production use with unrestricted shell access or enterprise deployment is not yet recommended.

---

## Tech Stack

| Component | Technology |
|:---|:---|
| LLM Gateway | [OpenRouter](https://openrouter.ai/) — unified access to all major LLMs |
| File Sync | [Syncthing](https://syncthing.net/) — encrypted peer-to-peer sync |
| App Integrations | [Composio](https://composio.dev) — OAuth-based 1,000+ app connectors |
| Scheduling | [APScheduler](https://github.com/agronholm/apscheduler) — cron-based task engine |
| HTTP Framework | [FastAPI](https://fastapi.tiangolo.com/) + SSE for real-time streaming |
| Desktop App | [Electron](https://www.electronjs.org/) + React + Vite + shadcn/ui |
| Feishu | [lark-oapi](https://github.com/larksuite/oapi-sdk-python) — official SDK |
| Telegram | [pyTelegramBotAPI](https://github.com/eternnoir/pyTelegramBotAPI) |
| Web Search | [ddgs](https://github.com/deedy5/duckduckgo_search) — DuckDuckGo |
| Package Manager | [uv](https://docs.astral.sh/uv/) |

## License

Apache License 2.0
