# Nono CoWork — Desktop Client

Electron-based desktop client for [Nono CoWork](../README.md). Connects to your VPS backend via HTTP + SSE.

## Tech Stack

| Layer | Technology |
|:---|:---|
| Shell | [Electron](https://www.electronjs.org/) |
| UI | React 19 + TypeScript |
| Build | [Vite](https://vite.dev/) |
| Styling | [Tailwind CSS](https://tailwindcss.com/) 4 + [shadcn/ui](https://ui.shadcn.com/) |
| Streaming | SSE (Server-Sent Events) |
| File Sync | Embedded [Syncthing](https://syncthing.net/) runtime (Windows) |

## Development

### Prerequisites

- [Node.js](https://nodejs.org/) ≥ 18
- A running Nono CoWork backend on your VPS ([Quick Start](../README.md#quick-start))

### Getting Started

```bash
cd desktop
npm install
npm run electron:dev
```

This starts the Vite dev server and Electron concurrently. Code changes are reflected immediately via HMR (Hot Module Replacement).

### Available Scripts

| Script | Description |
|:---|:---|
| `npm run electron:dev` | Start Electron + Vite dev server with HMR |
| `npm run dev` | Start Vite dev server only (opens in browser, no Electron) |
| `npm run build` | Compile TypeScript and bundle the frontend to `dist/` |
| `npm run package` | Build + package into distributable installer (`.exe` / `.dmg`) |
| `npm run lint` | Run ESLint |
| `npm run syncthing:prepare:win` | Download Syncthing binary for Windows embedded runtime |

### Building for Distribution

```bash
# Windows — produces NSIS installer + portable .exe
npm run package
# Output: desktop/release/

# macOS — produces .dmg (must be built on macOS)
npm run package
```

The build configuration is defined in the `"build"` section of `package.json`:
- **Windows**: NSIS installer + portable executable
- **macOS**: DMG disk image
- Syncthing binary is automatically bundled via `extraResources`

### Embedding Syncthing (Windows)

The desktop app can run a managed Syncthing process on Windows, eliminating the need for users to install SyncTrayzor separately.

```bash
npm run syncthing:prepare:win
```

This downloads `syncthing.exe` into `electron/vendor/syncthing/windows-amd64/` and is included by `electron-builder` as an app resource.

> Syncthing is licensed under MPL-2.0. The LICENSE file is bundled alongside the binary.

## Project Structure

```
desktop/
├── electron/                # Electron main process
│   ├── main.cjs             #   Main process entry point
│   ├── preload.cjs           #   Preload script (IPC bridge to renderer)
│   └── vendor/              #   Embedded binaries (Syncthing)
├── src/                     # React frontend (renderer process)
│   ├── components/          #   UI components
│   │   ├── ai-elements/     #     Chat message rendering
│   │   └── ui/              #     shadcn/ui base components
│   ├── pages/               #   Page views (Chat, Workspace, Routines, Settings)
│   ├── App.tsx              #   Root component + routing
│   └── index.css            #   Global styles + Tailwind config
├── scripts/                 # Build helpers
│   ├── free-port.cjs        #   Kill processes occupying dev port
│   └── fetch-syncthing-win.ps1  # Download Syncthing for Windows
└── package.json             # Dependencies + build config
```

## Configuration

On first launch, go to **Settings** (sidebar) and configure:

| Field | Value |
|:---|:---|
| Server Address | Your VPS URL, e.g. `http://your-vps-ip:8080` |
| Access Token | `DESKTOP_API_TOKEN` from your VPS `.env` |

Click **Test Connection** → **Save & Reconnect**. Config is persisted locally.

## Related Documentation

- [Desktop Setup Guide](../docs/desktop_setup.md) — End-user setup instructions
- [Syncthing Setup](../docs/syncthing_setup.md) — File sync configuration
- [Backend Quick Start](../README.md#quick-start) — VPS backend setup
