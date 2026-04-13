# Desktop App Setup Guide

The Nono CoWork desktop app connects to your VPS backend and provides a full-featured UI for chatting with the agent, managing automations, reviewing notifications, and monitoring file sync status.

## Prerequisites

- A running Nono CoWork backend ([Quick Start](../README.md#quick-start))
- Your VPS address and API token (from `.env`)

## 1. Install & Run (Development)

```bash
cd desktop
npm install
npm run electron:dev
```

## 2. Connect to Your VPS

On first launch, click **Settings** (bottom of the sidebar) and enter:

- **Server Address** — Your VPS URL (e.g., `http://your-vps-ip:8080`)
- **Access Token** — The `API_TOKEN` value from your VPS `.env` file

Click **Test Connection** to verify, then **Save & Reconnect**.

> 💡 The config is saved locally. You only need to do this once.

## 3. File Sync (Automatic Pairing)

If [Syncthing is running](syncthing_setup.md) on both your VPS and local machine, the desktop app will automatically pair them using the API connection — **no manual Device ID exchange needed**.

### Windows embedded Syncthing (built-in runtime)

On Windows, the desktop app can run a managed Syncthing process directly (no separate SyncTrayzor install required), when `syncthing.exe` is bundled with the app.

For local packaging, prepare the binary first:

```bash
cd desktop
npm run syncthing:prepare:win
```

The binary is copied into `electron/vendor/syncthing/windows-amd64/` and included by `electron-builder` as an app resource.

> License note: Syncthing is MPL-2.0. Keep the upstream license text in your distribution package.

The sync status indicator at the bottom of the sidebar shows:

| Status | Meaning |
|:---|:---|
| 🟢 Synced | Connected and up-to-date |
| 🔵 Syncing... | File transfer in progress |
| ⬜ Disconnected | Local Syncthing not running or VPS unreachable |

### What gets auto-paired?

When the desktop app calls `POST /api/sync/pair`:
1. Your local Syncthing Device ID is sent to the VPS
2. The VPS adds your device as trusted and shares all configured folders
3. The VPS Device ID is returned so your local Syncthing can connect back
4. P2P encrypted sync begins automatically

### Still need manual Syncthing setup?

The auto-pairing handles device exchange, but you still need:
- **Syncthing installed** on both your local machine and VPS ([guide](syncthing_setup.md))
- **A shared folder** configured on the VPS side

## 4. Features Overview

### Chat
Full conversational interface with the agent. Supports streaming responses, model switching, code blocks, and file deliverables.

### Workspace
Notification center for agent-initiated tasks. Review email drafts, file reports, and other deliverables. Approve or dismiss with one click.

### Routines
Manage automated workflows:
- **Cron schedules** — Time-based recurring tasks
- **Event triggers** — React to emails, app events, etc.
- **File watchers** — Process files dropped into sync folders

### Settings
- Server connection configuration
- Real-time sync status monitoring
