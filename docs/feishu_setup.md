English | [简体中文](feishu_setup_zh-CN.md)

# Feishu (Lark) Bot Setup Guide

## Step 1: Create an App on Feishu Open Platform (~15 min)

### 1.1 Create Application
1. Go to [Feishu Open Platform](https://open.feishu.cn/) and log in
2. Click "Create Custom App" in the top right
3. Fill in:
   - App Name: `VPS Agent` (anything you like)
   - Description: `Remote server AI assistant`

### 1.2 Get Credentials
1. Go to your app → "Credentials & Basic Info" in the left sidebar
2. Copy the **App ID** (format: `cli_xxxxxxxxxx`) and **App Secret**
3. Add them to `.env` on your VPS:
   ```
   FEISHU_APP_ID=cli_xxxxxxxxxx
   FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxx
   ```

### 1.3 Enable Bot Capability
1. Left sidebar → "App Capabilities" → "Add Capability"
2. Find **"Bot"** and enable it

### 1.4 Configure Permissions
1. Left sidebar → "Permissions & Scopes"
2. Search for and enable the following permissions:

| Permission | Description |
|------|------|
| `im:message` | Basic messaging |
| `im:message:send_as_bot` | Send messages as bot |
| `im:message.p2p_msg:readonly` | Receive direct messages |
| `im:message.group_at_msg:readonly` | Receive group @mentions |
| `im:message:readonly` | Read message details |

### 1.5 Configure Event Subscription (Critical Step)
1. Left sidebar → "Events & Callbacks"
2. **Subscription method** → Select **"Long Connection (WebSocket)"** ⚠️ This is important
3. Click "Add Event" → Search for `im.message.receive_v1` → Add it
4. Save

### 1.6 Publish the App
1. Left sidebar → "Version Management & Release"
2. Click "Create Version" → enter version number (e.g. `1.0.0`) and release notes
3. Submit for review
4. **Wait for approval** (internal enterprise apps are usually approved within minutes to hours)

> ⚠️ **The app must be published** for WebSocket connections to work. Unpublished apps will show "app has not established long connection".

### 1.7 Start Using
- **Direct message**: Search for your bot name in Feishu and send a message directly
- **Group chat**: Add the bot to a group, then @mention it with commands

---

## Step 2: Start on VPS

```bash
# Make sure .env has FEISHU_APP_ID and FEISHU_APP_SECRET

# Option 1: Run directly
cd /path/to/nono-cowork
.venv/bin/python -m src.channels.feishu

# Option 2: Via uv entry point
uv run feishu-bot
```

You should see this output on successful startup:
```
==================================================
🚀 Feishu Bot started (WebSocket long connection)
   App ID: cli_xxxxxx...
   Allowed users: not set
==================================================
Waiting for Feishu messages...
```

---

## Step 3: Optional Security Configuration

### Restrict Allowed Users
To limit who can use the bot, add a user whitelist in `.env`:

```bash
# The user's open_id can be found in VPS logs when they message the bot
FEISHU_ALLOWED_USERS=ou_xxx,ou_yyy
```

### Run in Background (Recommended)

**Option 1: systemd service (recommended)**

Use the unified multi-channel service file:

```bash
# Edit nono-cowork.service: replace YOUR_USERNAME with your actual username
# Set CHANNELS=feishu in .env (or add feishu to existing channels)
sudo cp nono-cowork.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nono-cowork
```

**Option 2: screen / tmux**

```bash
# Using screen
screen -S feishu-bot
cd /path/to/nono-cowork
uv run feishu-bot
# Ctrl+A, D to detach

# Or using tmux
tmux new -s feishu-bot
cd /path/to/nono-cowork
uv run feishu-bot
# Ctrl+B, D to detach
```

---

## Special Commands

| Command | Action |
|------|------|
| `/reset` or `reset` | Clear session context |
| `/help` or `help` | Show help message |

---

## Troubleshooting

### Q: "App has not established long connection"
A: The app must be published first (Step 1.6). WebSocket only works after approval.

### Q: Not receiving messages
A: Check the following:
1. Have you added the `im.message.receive_v1` event subscription?
2. Did you select "Long Connection (WebSocket)" mode?
3. Are all permissions enabled?
4. Is the app published and approved?

### Q: How to get a user's open_id
A: Start the Feishu Bot, have the target user send a message to the bot, and check the VPS logs for `from ou_xxx`.
