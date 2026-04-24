English | [简体中文](syncthing_setup_zh-CN.md)

# Syncthing File Sync Setup Guide

Syncthing enables automatic file synchronization between your local computer and VPS. When the Agent modifies files on the VPS, they automatically appear on your local machine.

## How It Works

```
Your Computer                  VPS
┌──────────────┐        ┌──────────────┐
│  ~/Sync      │◄──────►│  ~/Sync      │
│  (your files) │        │ (Agent works  │
│              │        │  here)        │
└──────────────┘        └──────────────┘
       ▲  Syncthing auto bidirectional sync  ▲
```

## 1. Install Syncthing on VPS

### Debian / Ubuntu

```bash
# Add official repository
sudo mkdir -p /etc/apt/keyrings
curl -L -o /etc/apt/keyrings/syncthing-archive-keyring.gpg \
  https://syncthing.net/release-key.gpg
echo "deb [signed-by=/etc/apt/keyrings/syncthing-archive-keyring.gpg] \
  https://apt.syncthing.net/ syncthing stable" | \
  sudo tee /etc/apt/sources.list.d/syncthing.list

sudo apt update
sudo apt install syncthing
```

### Start and Enable on Boot

```bash
# Set up as a user service
systemctl --user enable syncthing
systemctl --user start syncthing

# Check status
systemctl --user status syncthing
```

> ⚠️ **Running as root?** `systemctl --user` does not work for the root user by default. Use the system-wide service instead:
> ```bash
> sudo systemctl enable syncthing@root
> sudo systemctl start syncthing@root
> ```

### Open Firewall Ports

```bash
sudo ufw allow 22000/tcp   # Syncthing file sync
sudo ufw allow 21027/udp   # Syncthing discovery protocol
```

### Remote Access to Web UI (Optional)

By default, Syncthing Web UI only listens on `127.0.0.1:8384`. To access it from your local browser:

```bash
# Method 1 (recommended): SSH port forwarding
ssh -L 8384:localhost:8384 your-user@your-vps-ip
# Then open http://localhost:8384 in your local browser

# Method 2: Change listen address (not recommended, less secure)
# Edit ~/.local/state/syncthing/config.xml
# Change <address>127.0.0.1:8384</address> to <address>0.0.0.0:8384</address>
# ⚠️ Make sure to set a Web UI password
```

## 2. Install Syncthing on Your Local Machine

- **Windows (with Desktop App)**: The Nono CoWork desktop app can embed Syncthing — no separate install needed. See [Desktop App Setup](desktop_setup.md).
- **Windows (standalone)**: Download [SyncTrayzor](https://github.com/canton7/SyncTrayzor/releases) (Syncthing with a system tray icon)
- **macOS**: `brew install syncthing` or download from [syncthing.net](https://syncthing.net/)
- **Linux**: Same as VPS installation above

After starting, open the Web UI at http://localhost:8384.

## 3. Pair Devices

> 💡 **Using the Desktop App?** You can skip this section. The desktop app automatically exchanges Device IDs with the VPS via `Settings → Save & Reconnect`. See [Desktop App Setup](desktop_setup.md#3-file-sync-automatic-pairing).

### Manual pairing (without Desktop App):

1. **Get your VPS Device ID**: In the VPS Web UI → top right "Actions" → "Show ID"

2. **Add VPS device on your local machine**: Local Web UI → "Add Remote Device" → paste the VPS Device ID

3. **Confirm on VPS**: The VPS Web UI will show a confirmation prompt — click "Add"

## 4. Create a Shared Folder

1. Create a folder on the VPS:
   ```bash
   mkdir -p ~/Sync
   ```

2. In the VPS Web UI → "Add Folder":
   - Folder Label: `Sync` (any name you like)
   - Folder Path: `/home/your-username/Sync`
   - In the "Sharing" tab, check your local device

3. The local Web UI will show a sharing request — accept and choose a local target path

## 5. Configure Agent's Syncthing API Key

The Agent needs the Syncthing REST API to query sync status.

```bash
# Get API Key (run on VPS)
grep apikey ~/.local/state/syncthing/config.xml
# Output looks like: <apikey>xxxxxxxxxxxxxxxxxxxxx</apikey>

# Add to .env
SYNCTHING_API_KEY=xxxxxxxxxxxxxxxxxxxxx
```

## 6. Recommended .stignore Configuration

Create a `.stignore` file in the synced folder to exclude unnecessary content.

> **Note:** The Agent automatically ensures essential patterns are present via `_ensure_stignore()` at startup, but it's good practice to configure a comprehensive `.stignore` from the start.

The `(?d)` prefix tells Syncthing to also delete remote copies of newly-ignored files. The `**` pattern matches any number of subdirectories.

```
// Python virtual environments (wildcard covers .venv, .venv2, .blog_venv, etc.)
(?d)**/*venv*
(?d)**/env

// Python caches and build artifacts
(?d)**/__pycache__
(?d)**/*.pyc
(?d)**/*.pyo
(?d)**/*.egg-info

// Node.js
(?d)**/node_modules

// IDE and system files
(?d)**/.idea
(?d)**/.vscode
(?d)**/.DS_Store
(?d)**/Thumbs.db

// Git repos
(?d)**/.git

// Agent internals
(?d).agent_snapshots
(?d).stversions

// Large binary files (prevent accidental sync)
(?d)**/*.zip
(?d)**/*.tar.gz
(?d)**/*.mp4
```

## 7. Verify Sync

1. Create a test file in your local synced folder:
   ```bash
   echo "hello from local" > ~/Sync/test.txt
   ```

2. After a few seconds, check on VPS:
   ```bash
   cat ~/Sync/test.txt
   ```

3. In the Agent, send a command like: `check sync status` → Agent will call `sync_status()` to confirm

Once sync is working, you can place work files in the synced folder and instruct the Agent via Feishu/Telegram to process them!
