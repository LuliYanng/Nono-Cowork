English | [简体中文](README_zh-CN.md)

# 🤖 Nono CoWork

**Secure · Cloud-based · Always available — your 24/7 AI assistant.**

Nono CoWork is a self-hosted AI Agent that runs on your VPS and processes your local files via [Syncthing](https://syncthing.net/) — P2P encrypted, no third-party servers. Command it from Telegram, Feishu, or Terminal, anytime, anywhere.

<!-- Replace with your demo GIF -->
<!-- ![Nono CoWork Demo](docs/assets/demo.gif) -->

---

## Why This Exists

Most AI Agents that handle local files face an awkward trade-off:

- **Deploy on VPS** → Safe, but can't access your everyday files
- **Deploy locally** → Useful, but one hallucination away from `rm -rf` your system

Nono CoWork takes a different approach: **file sync**.

[Syncthing](https://syncthing.net/) has been around for over a decade — bidirectional sync, P2P encrypted, zero third-party involvement. So instead of giving an LLM root access to your machine:

1. Agent runs on your VPS (physically isolated — it can never touch your local system)
2. Your chosen folders sync to the VPS via Syncthing in real time
3. Send a command via Telegram / Feishu / Terminal — Agent starts working
4. Results sync back to your computer; your local changes sync to the Agent too

**The Agent only sees what you allow it to see.** This is the most thorough form of isolation — no sandbox, no permission system needed.

And because it's on a VPS, it's available 24/7. Send a message from your phone anytime. Got multiple devices? Syncthing syncs results to all of them.

---

## What It Can Do

| Your command | What the Agent does |
|:---|:---|
| Organize my expense reports folder | Reads receipts/invoices, categorizes by project, generates a summary |
| Analyze sales_data.xlsx | Writes a Python script, runs data analysis, generates a visual report |
| Search for the latest RAG developments | Searches the web, reads pages, summarizes key points |
| Write me a Flask API | Creates files, writes code, installs dependencies, tests the build |

## Architecture

```
Your phone/computer                    Your VPS
┌──────────────┐                ┌─────────────────────────┐
│  📱 Feishu    │──────────────►│  channels/feishu        │
│  📱 Telegram  │──────────────►│  channels/telegram      │
│  💻 Terminal  │──────────────►│  agent.py (CLI)         │
│              │                │         ↓               │
│              │                │  agent_runner (dispatch) │
│              │                │         ↓               │
│              │                │  agent_loop (LLM)       │
│              │                │         ↓               │
│              │                │  tools                  │
│              │                │   ├─ bash execution     │
│              │                │   ├─ file read/write    │
│              │                │   ├─ web search/read    │
│  📁 ~/Sync   │◄──Syncthing──►│   └─ sync awareness     │
│  (your files) │   bidirectional│       ↕                │
└──────────────┘                │  📁 ~/Sync (workspace)  │
                                └─────────────────────────┘
```

---

## Quick Start

**Requirements:** A Linux VPS (1 vCPU / 1GB minimum, 2 vCPU / 2GB recommended) · Python ≥ 3.12 · [uv](https://docs.astral.sh/uv/)

```bash
git clone https://github.com/KilYep/nono-cowork.git
cd nono-cowork
uv sync
cp .env.example .env   # then edit .env with your LLM API key
```

```bash
# Terminal mode (simplest)
uv run agent

# Telegram Bot
uv run telegram-bot

# Feishu (Lark) Bot
uv run feishu-bot
```

> 💡 Supports any LLM via [LiteLLM](https://github.com/BerriAI/litellm) — Qwen, Gemini, Claude, DeepSeek, GPT, and more. Just change one line in `.env`.

---

## Setup Guides

| Component | Guide |
|:---|:---|
| Telegram Bot | [docs/telegram_setup.md](docs/telegram_setup.md) |
| Feishu (Lark) Bot | [docs/feishu_setup.md](docs/feishu_setup.md) |
| Syncthing File Sync | [docs/syncthing_setup.md](docs/syncthing_setup.md) |

> Even without Syncthing, the Agent works fine on the VPS. Syncthing is what bridges your *local files* to the Agent.

---

## Key Features

- **Multi-LLM** — Switch models with one config line (Qwen, Gemini, Claude, DeepSeek, GPT). Prompt Caching support for compatible models
- **Multi-channel** — Feishu & Telegram built-in; [add new channels](docs/adding_channels.md) by implementing 3 methods
- **File sync** — Bidirectional Syncthing sync; results appear on your machine automatically
- **Full toolkit** — Bash commands, file read/write/edit, web search, webpage reading
- **Session management** — Independent sessions per user, concurrency control, context persistence
- **Observability** — Token usage tracking, prompt cache hit rate, structured JSON logs

## Tech Stack

| Component | Technology |
|:---|:---|
| LLM Interface | [LiteLLM](https://github.com/BerriAI/litellm) — unified multi-LLM API |
| File Sync | [Syncthing](https://syncthing.net/) — P2P encrypted sync |
| Feishu | [lark-oapi](https://github.com/larksuite/oapi-sdk-python) — official SDK, WebSocket |
| Telegram | [pyTelegramBotAPI](https://github.com/eternnoir/pyTelegramBotAPI) |
| Web Search | [ddgs](https://github.com/deedy5/duckduckgo_search) — DuckDuckGo |
| Package Manager | [uv](https://docs.astral.sh/uv/) |

## ⚠️ Security Notes

- ✅ Deploy on a **dedicated VPS** — don't run it on a machine with sensitive data
- ✅ Set `TELEGRAM_ALLOWED_USERS` / `FEISHU_ALLOWED_USERS` to **restrict access**
- ✅ Run as a **non-root user**
- ✅ If using Syncthing, only sync a **work folder** — not your entire home directory

## License

MIT
