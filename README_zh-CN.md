[English](README.md) | 简体中文

<h1 align="center">Nono CoWork</h1>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.12+-3776AB.svg?logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="https://github.com/KilYep/nono-cowork/stargazers"><img src="https://img.shields.io/github/stars/KilYep/nono-cowork?style=social" alt="GitHub stars"></a>
  <a href="https://github.com/KilYep/nono-cowork/commits/main"><img src="https://img.shields.io/github/last-commit/KilYep/nono-cowork" alt="Last Commit"></a>
</p>

<h3 align="center">为真实工作流而生的主动 Agent — 不只是浏览器任务。</h3>

<p align="center">一个运行在你 VPS 上的后台同事：监听事件、推进工作、把结果同步回你的本地工作区。</p>

大多数 Agent 还在等你发指令。Nono 在事情发生时就开始工作。

它可以监控你的邮箱、同步文件夹以及你接入的各种应用。当合作伙伴深夜发来一份合同，Nono 自动下载附件，从你的同步工作区里翻出去年的老版本逐条对比，标出关键条款变动，写好回复草稿。

早上打开电脑，桌面端已经有一张通知卡片在等你：*"合同已收到，3 处变更已标注，回复草稿待审核。"* 差异报告已经在你的本地文件夹里了。不用下载，不用登录什么后台——**文件就在你工作的地方。** 点击"发送"，邮件就出去了。

不在电脑前？Nono 也会通过 Telegram / 飞书通知你。

**这不是一个等你开口的助手，而是一个已经在干活的同事。**

<p align="center">
  <video src="https://github.com/user-attachments/assets/966b3346-12aa-4989-b041-d2571fd64fd5" width="800" controls autoplay loop muted></video>
</p>

---

## 为什么不一样

AI Agent 已经能做很多事。但大多数产品还停在这几种取舍里：

| 方案 | 问题 |
|:---|:---|
| **云端 Agent** | 24 小时在线，但文件在它们的云上。做完的东西还得你手动下载，搬回自己的工作流里。 |
| **桌面 Agent** | 能操作你的文件，但通常需要你的电脑一直开着，而且往往需要更大的本机访问权限。 |
| **自动化工具** | 擅长连接应用，但只能跑预设的 if-then 流程。 |

**Nono CoWork 换了一种思路：让 Agent 在你的 VPS 上保持在线，同时把产出物送回你已经在用的文件夹。**

- 🧠 **主动出击** — 监控邮件、文件变更和已接入的应用。重要事件发生时自主行动，不用你开口。
- ☁️ **持续在线** — 在你的 VPS 上持续运行，即使你合上笔记本电脑，工作也不会停。
- 📁 **文件直达桌面** — 结果直接同步到你的本地文件夹，产出物出现在你已经在工作的地方。
- 🔒 **隔离运行** — 运行在你的 VPS 上，不能直接控制你的本机设备，只能看到你明确同步出来的文件夹。
- ✋ **关键操作你说了算** — 邮件帮你写好了，但发不发由你点头。关键动作都会等你审核。

---

## 推进你的工作流

| 当这件事发生 | Nono 会先把这些做完 | 你最后只需要… |
| :--- | :--- | :--- |
| 📧 合作伙伴发来新合同 | 下载附件 → 调出相关版本 → 对比关键条款 → 起草回复邮件 | 看看差异，决定发不发 |
| 📬 客户三天没回消息 | 识别沉默线程 → 引用原始对话 → 起草一封礼貌的跟进邮件 | 点确认，让它发出 |
| 📊 你把一张数据表丢进本地工作文件夹 | 检测新文件 → 跑分析 → 生成图表和结论 → 保存成完整报告 | 直接打开报告 |
| 🗂️ 你的 inbox 堆满 PDF、截图和零散文档 | 识别内容类型 → 归类重命名 → 移到正确文件夹 | 偶尔检查一下结果 |

> 它不是等你提一个个任务，而是在事情发生之后，先把工作推进到只差你最后拍板。

---

## 架构

```text
  事件源 (24/7)                        你的 VPS
  ┌──────────────┐               ┌──────────────────────────────┐
  │ 📨 Gmail      │──Composio───►│                              │
  │ 📋 GitHub     │──WebSocket──►│   事件路由                    │
  │ 📅 Calendar   │──触发器────►│      ↓                       │
  │ 📁 文件变更   │──Syncthing──►│   Agent 引擎 (LLM)           │
  └──────────────┘               │      ↓                       │
                                 │   自主执行                    │
  控制渠道 (随时)                 │   ├─ 文件读写编辑             │
  ┌──────────────┐               │   ├─ Shell 命令执行           │
  │ 📱 Telegram   │─────────────►│   ├─ 网络搜索/网页阅读        │
  │ 📱 飞书       │─────────────►│   ├─ 1,000+ 应用 API 调用     │
  │ 🖥️ 桌面端    │──HTTP+SSE───►│   └─ 定时任务调度             │
  │ 💻 终端       │─────────────►│      ↓                       │
  └──────────────┘               │   通知系统                    │
                                 │   (结构化卡片 · 等待人工审核)  │
  你的设备                        │      ↓                       │
  ┌──────────────┐               │   📁 ~/Sync (Agent 工作区)   │
  │ 📁 ~/Sync    │◄──Syncthing──►│      ↕ 双向同步              │
  │ (你的文件)    │  加密 P2P     │                              │
  └──────────────┘               └──────────────────────────────┘
```

---

## 快速开始

**环境要求：** Linux VPS · Python ≥ 3.12 · [uv](https://docs.astral.sh/uv/) · [OpenRouter API Key](https://openrouter.ai/)

```bash
# 安装 uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

git clone https://github.com/KilYep/nono-cowork.git
cd nono-cowork
uv sync
cp .env.example .env   # 填入你的 OPENROUTER_API_KEY
```

```bash
# 启动选定的渠道（推荐）
CHANNELS=desktop,feishu,telegram uv run python src/main.py

# 或者单独运行某个渠道做测试
uv run agent            # 终端交互模式（最简单）
uv run feishu-bot       # 仅飞书
uv run telegram-bot     # 仅 Telegram
uv run desktop-agent    # 仅桌面端 API
```

持久化部署可以使用项目自带的 systemd service 文件：

```bash
# 先编辑 nono-cowork.service，将 YOUR_USERNAME 替换为你的实际用户名
sudo cp nono-cowork.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nono-cowork
```

> 💡 一个 Key，所有模型。[OpenRouter](https://openrouter.ai/) 统一路由到 Claude、GPT、Gemini、DeepSeek、通义千问等——在 `.env` 里改一行就能切换模型。

> **最快上手（不需要 Syncthing 或 Composio）：** 在 `.env` 里填入 `OPENROUTER_API_KEY`，然后跑 `uv run agent`。两分钟内就能在终端里得到一个可用的 Agent。准备好了再接 Syncthing（文件同步）和 Composio（应用触发）。

### 防火墙 / 端口

如果你的 VPS 使用了防火墙，需要放行相关端口：

```bash
sudo ufw allow 8080/tcp    # Desktop API（桌面端连接需要）
sudo ufw allow 22000/tcp   # Syncthing 文件同步
sudo ufw allow 21027/udp   # Syncthing 发现协议
sudo ufw allow 9090/tcp    # Composio webhook（使用事件触发器时需要）
```

---

## 配置指南

| 组件 | 文档 |
|:---|:---|
| 桌面端 | [docs/desktop_setup_zh-CN.md](docs/desktop_setup_zh-CN.md) |
| Syncthing 文件同步 | [docs/syncthing_setup_zh-CN.md](docs/syncthing_setup_zh-CN.md) |
| Telegram Bot | [docs/telegram_setup_zh-CN.md](docs/telegram_setup_zh-CN.md) |
| 飞书 Bot | [docs/feishu_setup_zh-CN.md](docs/feishu_setup_zh-CN.md) |
| Composio 应用集成 | [docs/composio_setup_zh-CN.md](docs/composio_setup_zh-CN.md) |
| 防火墙 / 端口 | 见上方[快速开始](#快速开始) |

---

## 核心能力

### 🔥 主动自动化（Routines）

- **定时调度** — "每天早上 8 点，编一份行业简报"
- **事件触发** — "收到 @partner.com 的邮件时，自动处理"
- **文件监听** — "当 /inbox/ 出现新文件时，分析它"
- **人工审核** — 结构化通知卡片，支持批准 / 驳回操作

### 🛠️ Agent 工具集

- **文件操作** — 读写编辑常见文件类型，包括 PDF、Excel、Word
- **命令执行** — 在 VPS 上执行 Shell 命令
- **网络能力** — 搜索互联网、提取网页内容
- **1,000+ 应用集成** — Gmail、GitHub、Slack、Notion 等，通过 [Composio](https://composio.dev) 接入
- **Syncthing 控制** — 查看同步状态、暂停/恢复、恢复文件历史版本
- **子 Agent 委派** — 为复杂任务启动隔离的 Agent 会话
- **持久化上下文** — 跨会话记住你的偏好和上下文

### 📡 多渠道接入

- **桌面端** — 基于 Electron 的 UI，支持实时 SSE 流、通知中心、自动化规则管理、内置设置和引导式 Syncthing 配对
- **Telegram** — 全功能机器人，支持内联操作
- **飞书** — 原生 WebSocket 长连接
- **终端** — 直接 CLI 交互

### 🔒 架构级安全

- Agent 运行在**隔离 VPS** 上，不能直接控制你的本机设备
- 文件通过 **Syncthing 加密 P2P 协议** 同步，不经过中心化存储服务
- **选择性同步** — Agent 只能看到你明确共享的文件夹
- **访问控制** — 限制特定的 Telegram / 飞书用户 ID
- **API Token 认证** — 桌面端 API 使用 Bearer Token 鉴权

---

## 项目状态

| 模块 | 状态 |
|:---|:---|
| 终端 / 桌面端 / Telegram / 飞书渠道 | ✅ 已实现 |
| 定时调度 & 事件触发 | ✅ 已实现 |
| Syncthing 文件同步与交付 | ✅ 已实现 |
| Composio 应用集成 | ✅ 已实现（依赖 Composio 上游） |
| 人工审核流程 | ✅ 已实现 |

> **当前阶段：Early Beta** — 适合个人工作流，如文档处理、邮件监控、文件自动化等。不建议用于无限制 Shell 访问或企业级部署。

---

## 技术栈

| 组件 | 技术 |
|:---|:---|
| LLM 接入 | [OpenRouter](https://openrouter.ai/) — 统一访问所有主流 LLM |
| 文件同步 | [Syncthing](https://syncthing.net/) — 加密 P2P 同步 |
| 应用集成 | [Composio](https://composio.dev) — OAuth 方式接入 1,000+ 应用 |
| 定时任务 | [APScheduler](https://github.com/agronholm/apscheduler) — cron 任务引擎 |
| HTTP 框架 | [FastAPI](https://fastapi.tiangolo.com/) + SSE 实时流 |
| 桌面端 | [Electron](https://www.electronjs.org/) + React + Vite + shadcn/ui |
| 飞书集成 | [lark-oapi](https://github.com/larksuite/oapi-sdk-python) — 官方 SDK |
| Telegram 集成 | [pyTelegramBotAPI](https://github.com/eternnoir/pyTelegramBotAPI) |
| 网络搜索 | [ddgs](https://github.com/deedy5/duckduckgo_search) — DuckDuckGo |
| 包管理 | [uv](https://docs.astral.sh/uv/) |

## License

Apache License 2.0
