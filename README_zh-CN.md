[English](README.md) | 简体中文

# 🤖 Nono CoWork

**安全 · 云端 · 随时随地 —— 你的 24 小时 AI 助理。**

Nono CoWork 是一个部署在你自己 VPS 上的 AI Agent，通过 [Syncthing](https://syncthing.net/) 同步你的本地文件 —— P2P 加密，不经过任何第三方服务器。飞书 / Telegram / 终端随时下达指令。

<!-- 替换成你的 Demo GIF -->
<!-- ![Nono CoWork Demo](docs/assets/demo.gif) -->

---

## 为什么做这个

大多数能处理本地文件的 AI Agent 都面临一个尴尬的二选一：

- **部署在 VPS 上** → 安全了，但 Agent 碰不到你的日常文件
- **部署在本地** → 能帮你整理文件了，但你得担心哪天它幻觉了，一个 `rm -rf` 把电脑搞崩

Nono CoWork 换了一个思路：**文件同步**。

[Syncthing](https://syncthing.net/) 已经做了十几年了 —— 双向同步、P2P 加密、不经过任何第三方服务器。所以，与其给 LLM 你电脑的 root 权限：

1. Agent 跑在 VPS 上（物理隔离，它永远碰不到你的本地系统）
2. 你想让它处理的文件夹，通过 Syncthing 与 VPS 实时双向同步
3. 在飞书 / Telegram / 终端发一句话，Agent 就开始干活
4. 处理完的文件自动同步回你电脑，你在本地的改动，Agent 在云端也能实时看到

**它只能看到你允许它看到的文件。** 不用容器沙盒，不用权限控制 —— 物理上就不在同一台机器，这是最彻底的隔离。

而且因为跑在 VPS 上，它可以 24 小时待命，你手机上发一条消息就行。多台电脑装了 Syncthing 的话，处理结果还会同步到所有设备上。

---

## 它能做什么

| 你发的指令 | Agent 做的事 |
|:---|:---|
| 帮我整理一下报销文件夹 | 读取所有发票/行程单，按项目分类，生成报销汇总表 |
| 分析一下 sales_data.xlsx | 写 Python 脚本、跑数据分析、生成可视化报告 |
| 搜索一下 RAG 最新进展 | 联网搜索、阅读网页、汇总要点 |
| 帮我写一个 Flask API | 创建文件、写代码、安装依赖、测试运行 |

## 架构

```
你的电脑/手机                          你的 VPS
┌──────────────┐                ┌─────────────────────────┐
│  📱 飞书      │───────────────►│  channels/feishu        │
│  📱 Telegram  │───────────────►│  channels/telegram      │
│  💻 终端      │───────────────►│  agent.py (CLI)         │
│              │                │         ↓               │
│              │                │  agent_runner (调度)     │
│              │                │         ↓               │
│              │                │  agent_loop (LLM 推理)  │
│              │                │         ↓               │
│              │                │  tools                  │
│              │                │   ├─ bash 命令执行       │
│              │                │   ├─ 文件读写编辑        │
│              │                │   ├─ 网络搜索/网页读取    │
│  📁 ~/Sync   │◄──Syncthing──►│   └─ 同步状态感知        │
│  （你的文件）  │   双向同步     │       ↕                 │
└──────────────┘                │  📁 ~/Sync（Agent 工作区）│
                                └─────────────────────────┘
```

---

## 快速开始

**环境要求：** VPS（最低 1核1G，推荐 2核2G）· Python ≥ 3.12 · [uv](https://docs.astral.sh/uv/)

```bash
git clone https://github.com/KilYep/nono-cowork.git
cd nono-cowork
uv sync
cp .env.example .env   # 然后编辑 .env，填入 LLM API Key
```

```bash
# 终端模式（最简单）
uv run agent

# Telegram Bot
uv run telegram-bot

# 飞书 Bot
uv run feishu-bot
```

> 💡 通过 [LiteLLM](https://github.com/BerriAI/litellm) 支持各种模型 —— 通义千问、Gemini、Claude、DeepSeek、GPT 等。在 `.env` 里改一行就能切换。

---

## 配置指南

| 组件 | 文档 |
|:---|:---|
| Telegram Bot | [docs/telegram_setup_zh-CN.md](docs/telegram_setup_zh-CN.md) |
| 飞书 Bot | [docs/feishu_setup_zh-CN.md](docs/feishu_setup_zh-CN.md) |
| Syncthing 文件同步 | [docs/syncthing_setup.md](docs/syncthing_setup.md) |

> 即使不配置 Syncthing，Agent 也可以正常使用。Syncthing 是让 Agent 能操作你「本地文件」的桥梁。

---

## 核心特性

- **多 LLM 支持** — 一行配置切换模型（通义千问、Gemini、Claude、DeepSeek、GPT），支持 Prompt Caching
- **多 IM 渠道** — 飞书、Telegram 开箱即用；新增渠道只需实现 3 个方法
- **文件同步** — Syncthing 双向同步，Agent 处理结果自动出现在你本地
- **完整工具集** — bash 命令、文件读写编辑、网络搜索、网页读取
- **会话管理** — 多用户独立会话、并发控制、上下文保持
- **可观测性** — Token 用量追踪、Prompt Cache 命中率、JSON 结构化日志

## 技术栈

| 组件 | 技术 |
|:---|:---|
| LLM 接口 | [LiteLLM](https://github.com/BerriAI/litellm) — 统一多 LLM 调用 |
| 文件同步 | [Syncthing](https://syncthing.net/) — P2P 加密同步 |
| 飞书集成 | [lark-oapi](https://github.com/larksuite/oapi-sdk-python) — 官方 SDK，WebSocket 长连接 |
| Telegram 集成 | [pyTelegramBotAPI](https://github.com/eternnoir/pyTelegramBotAPI) |
| 网络搜索 | [ddgs](https://github.com/deedy5/duckduckgo_search) — DuckDuckGo 搜索 |
| 包管理 | [uv](https://docs.astral.sh/uv/) |

## ⚠️ 安全提示

- ✅ 部署在**独立的 VPS** 上，不要放在存有敏感数据的机器上
- ✅ 配置 `TELEGRAM_ALLOWED_USERS` / `FEISHU_ALLOWED_USERS` **限制可用用户**
- ✅ 使用**非 root 用户**运行
- ✅ 如果使用 Syncthing，只同步**工作文件夹**，不要同步整个 home 目录

## License

MIT
