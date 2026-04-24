[English](telegram_setup.md) | 简体中文

# Telegram 机器人配置指南

## 1. 创建 Telegram Bot（2分钟）

1. 打开 Telegram，搜索 **@BotFather**
2. 发送 `/newbot`
3. 输入机器人名称（如 `My VPS Agent`）
4. 输入用户名（必须以 `bot` 结尾，如 `my_vps_agent_bot`）
5. BotFather 会返回 Token（格式 `123456789:ABCdefGHIjklMNO...`）
6. 填入 `.env`：
   ```
   TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNO...
   ```

## 2. 获取你的用户 ID（用于白名单）

1. 搜索 **@userinfobot** 并发送任意消息
2. 它会返回你的 User ID（一个数字）
3. 填入 `.env`（可选 —— 不填则不限制用户）：
   ```
   TELEGRAM_ALLOWED_USERS=123456789
   ```
   多个用户：`TELEGRAM_ALLOWED_USERS=111,222,333`

## 3. 启动

```bash
cd /path/to/nono-cowork

# 直接运行
.venv/bin/python -m src.channels.telegram

# 或使用 uv 入口
uv run telegram-bot
```

启动成功后会看到：
```
==================================================
🚀 Telegram Bot started (Polling mode)
   Bot: @my_vps_agent_bot
   Allowed users: 123456789
==================================================
```

## 4. 后台运行

### 方式一：screen / tmux

```bash
screen -S telegram-bot
cd /path/to/nono-cowork
.venv/bin/python -m src.channels.telegram
# Ctrl+A, D 脱离
```

### 方式二：systemd 服务（推荐）

使用统一的多渠道服务文件：

```bash
# 编辑 nono-cowork.service，将 YOUR_USERNAME 替换为你的实际用户名
# 在 .env 中设置 CHANNELS=telegram（或添加到已有渠道列表中）
sudo cp nono-cowork.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nono-cowork
```

## 5. 使用方法

- 在 Telegram 中找到你的 Bot，直接发送消息
- 发送 `/reset` 重置会话
- 发送 `/help` 查看帮助
