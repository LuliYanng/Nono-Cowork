# Desktop App Setup Guide

The Nono CoWork desktop app connects to your VPS backend and provides a full UI: chat with the agent, manage automations, review notifications, and monitor file sync.

## Prerequisites

- A running Nono CoWork backend on your VPS ([Quick Start](../README.md#quick-start))
- Your VPS address and API token (from `.env`)

## Install

### Option A: Download Release (Recommended)

> 🚧 Pre-built releases are coming soon. For now, use Option B below.

<!-- Download from [GitHub Releases](https://github.com/KilYep/nono-cowork/releases) when available. -->

### Option B: Build from Source

Requires [Node.js](https://nodejs.org/) ≥ 18.

```bash
cd desktop
npm install
npm run package
```

The installer will be in `desktop/release/`. Run it to install.

> For development, use `npm run electron:dev` instead — see [desktop/README.md](../desktop/README.md) for details.

## Connect to Your VPS

On first launch:

1. Click **Settings** in the sidebar
2. Enter your **Server Address** (e.g., `http://your-vps-ip:8080`)
3. Enter the **Access Token** — the `DESKTOP_API_TOKEN` value from your VPS `.env` file
4. Click **Test Connection** → **Save & Reconnect**

> 💡 The config is saved locally. You only need to do this once.

## File Sync

The desktop app integrates with [Syncthing](syncthing_setup.md) for automatic file synchronization between your VPS and local machine.

### Automatic Pairing

If Syncthing is running on both your VPS and local machine, the desktop app automatically exchanges Device IDs on connection — **no manual pairing needed**.

### Windows Embedded Syncthing

On Windows, the desktop app includes a built-in Syncthing runtime. No separate Syncthing installation required.

For development or self-built packages, prepare the binary first:

```bash
cd desktop
npm run syncthing:prepare:win
```

### Sync Status

The indicator at the bottom of the sidebar shows:

| Icon | Status |
|:---|:---|
| 🟢 Synced | Connected and up-to-date |
| 🔵 Syncing... | File transfer in progress |
| ⬜ Disconnected | Local Syncthing not running or VPS unreachable |

## Features

| Feature | Description |
|:---|:---|
| **Chat** | Conversational interface with streaming responses, model switching, and code blocks |
| **Workspace** | Notification center — review agent deliverables (email drafts, reports), approve or dismiss |
| **Routines** | Manage cron schedules, event triggers, and file watchers |
| **Settings** | Server connection, sync status, model selection |
