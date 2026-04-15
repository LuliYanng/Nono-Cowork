const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const { spawn } = require('child_process');

// ── Local config persistence ──
// Stores { apiBase, apiToken } in userData directory
function getConfigPath() {
  return path.join(app.getPath('userData'), 'nono-config.json');
}

function readConfig() {
  try {
    const configPath = getConfigPath();
    if (fs.existsSync(configPath)) {
      return JSON.parse(fs.readFileSync(configPath, 'utf8'));
    }
  } catch (err) {
    console.error('Failed to read config:', err.message);
  }
  return null;
}

function writeConfig(config) {
  try {
    const configPath = getConfigPath();
    fs.mkdirSync(path.dirname(configPath), { recursive: true });
    fs.writeFileSync(configPath, JSON.stringify(config, null, 2), 'utf8');
    return true;
  } catch (err) {
    console.error('Failed to write config:', err.message);
    return false;
  }
}

const MANAGED_SYNCTHING_PORT = Number(process.env.NONO_SYNCTHING_GUI_PORT || '52784');
const MANAGED_SYNCTHING_STARTUP_TIMEOUT_MS = 20_000;
let appIsQuitting = false;

let syncthingRuntime = {
  managed: false,
  baseUrl: process.env.NONO_SYNCTHING_URL || 'http://127.0.0.1:8384',
  configPath: null,
};

let managedSyncthingProcess = null;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function getExternalSyncthingConfigPath() {
  if (process.platform === 'win32') {
    return path.join(process.env.LOCALAPPDATA || '', 'Syncthing', 'config.xml');
  }
  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'Syncthing', 'config.xml');
  }
  return path.join(os.homedir(), '.local', 'state', 'syncthing', 'config.xml');
}

function getManagedSyncthingHomePath() {
  return path.join(app.getPath('userData'), 'syncthing-home');
}

function getManagedSyncthingConfigPath() {
  return path.join(getManagedSyncthingHomePath(), 'config.xml');
}

function getManagedSyncthingExeCandidates() {
  const out = [];
  if (process.env.NONO_SYNCTHING_EXE) {
    out.push(process.env.NONO_SYNCTHING_EXE);
  }
  // Packaged app: extraResources/syncthing/syncthing.exe
  out.push(path.join(process.resourcesPath, 'syncthing', 'syncthing.exe'));
  // Dev mode: repository copy
  out.push(path.join(__dirname, 'vendor', 'syncthing', 'windows-amd64', 'syncthing.exe'));
  return out;
}

function findManagedSyncthingExe() {
  for (const p of getManagedSyncthingExeCandidates()) {
    if (p && fs.existsSync(p)) return p;
  }
  return '';
}

function readSyncthingApiKeyFromConfig(configPath) {
  try {
    if (!configPath || !fs.existsSync(configPath)) return '';
    const xml = fs.readFileSync(configPath, 'utf8');
    const match = xml.match(/<apikey>([^<]+)<\/apikey>/);
    return match ? match[1] : '';
  } catch {
    return '';
  }
}

function readSyncthingApiKey() {
  const configPath = syncthingRuntime.configPath || getExternalSyncthingConfigPath();
  return readSyncthingApiKeyFromConfig(configPath);
}

function syncthingRequest(method, endpoint, body, opts = {}) {
  return new Promise((resolve, reject) => {
    const baseUrl = opts.baseUrl || syncthingRuntime.baseUrl || 'http://127.0.0.1:8384';
    const apiKey = opts.apiKey != null ? opts.apiKey : readSyncthingApiKey();
    const requestUrl = new URL(endpoint, baseUrl);
    const payload = body ? JSON.stringify(body) : null;
    const headers = {
      ...(apiKey ? { 'X-API-Key': apiKey } : {}),
      ...(payload ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } : {}),
    };

    const req = http.request(
      requestUrl,
      {
        method,
        headers,
        timeout: 5000,
      },
      (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          const ok = (res.statusCode || 500) >= 200 && (res.statusCode || 500) < 300;
          if (!ok) {
            reject(new Error(`Syncthing API ${method} ${endpoint} failed: HTTP ${res.statusCode}`));
            return;
          }
          if (!data) {
            resolve({});
            return;
          }
          try {
            resolve(JSON.parse(data));
          } catch {
            resolve({});
          }
        });
      }
    );

    req.on('error', () => reject(new Error('Local Syncthing not reachable')));
    req.on('timeout', () => {
      req.destroy();
      reject(new Error('Local Syncthing request timed out'));
    });

    if (payload) req.write(payload);
    req.end();
  });
}

async function waitForManagedSyncthingReady() {
  const deadline = Date.now() + MANAGED_SYNCTHING_STARTUP_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const apiKey = readSyncthingApiKeyFromConfig(getManagedSyncthingConfigPath());
    if (apiKey) {
      try {
        await syncthingRequest('GET', '/rest/system/status', null, {
          baseUrl: `http://127.0.0.1:${MANAGED_SYNCTHING_PORT}`,
          apiKey,
        });
        return;
      } catch {
        // Keep retrying until timeout
      }
    }
    await sleep(400);
  }
  throw new Error('Managed Syncthing startup timed out');
}

async function initializeSyncthingRuntime() {
  syncthingRuntime = {
    managed: false,
    baseUrl: process.env.NONO_SYNCTHING_URL || 'http://127.0.0.1:8384',
    configPath: getExternalSyncthingConfigPath(),
  };

  // Windows-first embedded mode (can be disabled with NONO_MANAGED_SYNCTHING=0)
  if (process.platform !== 'win32' || process.env.NONO_MANAGED_SYNCTHING === '0') {
    return;
  }

  const exePath = findManagedSyncthingExe();
  if (!exePath) {
    console.warn('[Syncthing] Embedded binary not found, fallback to external Syncthing instance.');
    return;
  }

  const homePath = getManagedSyncthingHomePath();
  fs.mkdirSync(homePath, { recursive: true });

  // Pre-seed a minimal config.xml on first run so Syncthing does NOT
  // auto-create a "Default Folder" (which uses path="~" and causes
  // ghost folder issues on Windows where ~ becomes a literal directory).
  const seedConfigPath = path.join(homePath, 'config.xml');
  if (!fs.existsSync(seedConfigPath)) {
    const minimalConfig = `<configuration version="35">
    <gui enabled="true" tls="false" debugging="false">
        <address>127.0.0.1:${MANAGED_SYNCTHING_PORT}</address>
    </gui>
</configuration>`;
    fs.writeFileSync(seedConfigPath, minimalConfig, 'utf8');
    console.info('[Syncthing] Pre-seeded empty config (no default folder)');
  }

  managedSyncthingProcess = spawn(
    exePath,
    [
      'serve',
      '--home', homePath,
      '--no-browser',
      // Prevent auto-creation of default folder (v1 flag, harmless on v2)
      '--no-default-folder',
      '--gui-address', `127.0.0.1:${MANAGED_SYNCTHING_PORT}`,
    ],
    {
      windowsHide: true,
      stdio: 'ignore',
    }
  );

  managedSyncthingProcess.on('exit', (code, signal) => {
    if (!appIsQuitting) {
      console.warn(`[Syncthing] Embedded process exited unexpectedly (code=${code}, signal=${signal})`);
    }
    managedSyncthingProcess = null;
  });

  try {
    await waitForManagedSyncthingReady();
    syncthingRuntime = {
      managed: true,
      baseUrl: `http://127.0.0.1:${MANAGED_SYNCTHING_PORT}`,
      configPath: getManagedSyncthingConfigPath(),
    };
    console.info('[Syncthing] Embedded mode enabled');
  } catch (err) {
    console.error('[Syncthing] Embedded startup failed, fallback to external:', err.message);
    if (managedSyncthingProcess) {
      try { managedSyncthingProcess.kill(); } catch {}
      managedSyncthingProcess = null;
    }
    syncthingRuntime = {
      managed: false,
      baseUrl: process.env.NONO_SYNCTHING_URL || 'http://127.0.0.1:8384',
      configPath: getExternalSyncthingConfigPath(),
    };
  }
}

async function shutdownManagedSyncthing() {
  if (!managedSyncthingProcess) return;
  try {
    const apiKey = readSyncthingApiKeyFromConfig(getManagedSyncthingConfigPath());
    if (apiKey) {
      await syncthingRequest('POST', '/rest/system/shutdown', null, {
        baseUrl: `http://127.0.0.1:${MANAGED_SYNCTHING_PORT}`,
        apiKey,
      });
    }
  } catch {
    // Ignore shutdown API failures
  }

  await sleep(700);
  if (managedSyncthingProcess) {
    try { managedSyncthingProcess.kill(); } catch {}
    managedSyncthingProcess = null;
  }
}

async function getLocalSyncthingStatus() {
  const [systemStatus, folders] = await Promise.all([
    syncthingRequest('GET', '/rest/system/status'),
    syncthingRequest('GET', '/rest/config/folders'),
  ]);

  return {
    deviceId: systemStatus.myID || '',
    folders: (Array.isArray(folders) ? folders : [])
      .filter((f) => f.id)  // Skip ghost folders with empty IDs
      .map((f) => ({
        id: f.id,
        label: f.label || f.id,
        path: path.resolve(f.path),  // Resolve ~ or relative paths to absolute
      })),
  };
}

async function ensureLocalSyncthingRemoteDevice(deviceId, deviceName) {
  const trimmedId = (deviceId || '').trim();
  if (!trimmedId) {
    throw new Error("Missing 'deviceId'");
  }

  const name = (deviceName || '').trim() || 'Nono CoWork VPS';
  const config = await syncthingRequest('GET', '/rest/config');
  const devices = Array.isArray(config.devices) ? config.devices : [];
  const existing = devices.find((d) => d && d.deviceID === trimmedId);

  if (!existing) {
    await syncthingRequest('POST', '/rest/config/devices', {
      deviceID: trimmedId,
      name,
      autoAcceptFolders: false,
    });
  }

  return { added: !existing };
}

/**
 * Generate a short random folder ID for Syncthing (e.g. "nono-a3f7b").
 */
function generateFolderId() {
  const hex = require('crypto').randomBytes(4).toString('hex');
  return `nono-${hex}`;
}

/**
 * Add a local folder to Syncthing and share it with the VPS device.
 * Returns { folderId, folderLabel, localPath }.
 */
async function addLocalSyncFolder(localPath, vpsDeviceId) {
  if (!localPath || !vpsDeviceId) {
    throw new Error('Missing localPath or vpsDeviceId');
  }

  const folderLabel = path.basename(localPath);
  const folderId = generateFolderId();

  // Check if this path is already synced
  const existing = await syncthingRequest('GET', '/rest/config/folders');
  const folders = Array.isArray(existing) ? existing : [];
  const alreadySynced = folders.find(
    (f) => path.resolve(f.path) === path.resolve(localPath)
  );
  if (alreadySynced) {
    return {
      folderId: alreadySynced.id,
      folderLabel: alreadySynced.label || alreadySynced.id,
      localPath: alreadySynced.path,
      alreadyExists: true,
    };
  }

  await syncthingRequest('POST', '/rest/config/folders', {
    id: folderId,
    label: folderLabel,
    path: localPath,
    devices: [{ deviceID: vpsDeviceId }],
    rescanIntervalS: 60,
    fsWatcherEnabled: true,
    fsWatcherDelayS: 1,
  });

  return { folderId, folderLabel, localPath, alreadyExists: false };
}

/**
 * List all Syncthing folders that are shared with a specific device.
 */
async function listSyncFolders(vpsDeviceId) {
  const folders = await syncthingRequest('GET', '/rest/config/folders');
  if (!Array.isArray(folders)) return [];

  return folders
    .filter((f) => (f.devices || []).some((d) => d.deviceID === vpsDeviceId))
    .map((f) => ({
      id: f.id,
      label: f.label || f.id,
      path: f.path,
    }));
}

/**
 * Remove a synced folder from local Syncthing config.
 */
async function removeSyncFolder(folderId) {
  await syncthingRequest('DELETE', `/rest/config/folders/${folderId}`);
  return { removed: true };
}

function createWindow() {
  const mainWindow = new BrowserWindow({
    width: 1000,
    height: 700,
    minWidth: 520,
    minHeight: 400,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.cjs'),
    },
    // Frameless modern look
    frame: false,
    titleBarStyle: 'hidden',
    title: 'Nono CoWork',
  });

  // Window control handlers
  ipcMain.on('window-minimize', () => mainWindow.minimize());
  ipcMain.on('window-maximize', () => {
    if (mainWindow.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow.maximize();
    }
  });
  ipcMain.on('window-close', () => mainWindow.close());

  // ── File system IPC handlers (for deliverable components) ──

  // Normalize path: ensure Windows-style backslashes and resolve to absolute
  function normalizePath(p) {
    if (!p) return p;
    // Convert forward slashes to backslashes on Windows
    if (process.platform === 'win32') {
      p = p.replace(/\//g, '\\');
    }
    // Resolve to absolute path
    return path.resolve(p);
  }

  // Open file with system default application
  ipcMain.handle('fs-open-file', async (_event, filePath) => {
    try {
      const normalized = normalizePath(filePath);


      // Check if the file actually exists
      if (!fs.existsSync(normalized)) {
        return { success: false, error: `File not found: ${normalized}` };
      }

      const errMsg = await shell.openPath(normalized);
      if (errMsg) {
        return { success: false, error: errMsg };
      }
      return { success: true };
    } catch (err) {
      return { success: false, error: err.message };
    }
  });

  // Open a folder in file manager
  ipcMain.handle('fs-open-folder', async (_event, folderPath) => {
    try {
      const normalized = normalizePath(folderPath);


      if (!fs.existsSync(normalized)) {
        return { success: false, error: `Folder not found: ${normalized}` };
      }

      const errMsg = await shell.openPath(normalized);
      if (errMsg) {
        return { success: false, error: errMsg };
      }
      return { success: true };
    } catch (err) {
      return { success: false, error: err.message };
    }
  });

  // Show file in file manager (highlight the file)
  ipcMain.handle('fs-show-in-explorer', async (_event, filePath) => {
    try {
      const normalized = normalizePath(filePath);


      // Check if the file/folder exists — showItemInFolder silently fails otherwise
      if (!fs.existsSync(normalized)) {
        // Try opening the parent directory if the file doesn't exist yet
        const parentDir = path.dirname(normalized);
        if (fs.existsSync(parentDir)) {
          shell.showItemInFolder(parentDir);
          return { success: true };
        }
        return { success: false, error: `Path not found: ${normalized}` };
      }

      shell.showItemInFolder(normalized);
      return { success: true };
    } catch (err) {
      return { success: false, error: err.message };
    }
  });

  // Query LOCAL Syncthing API to get local folder paths (zero-config)
  ipcMain.handle('syncthing-local-folders', async () => {
    try {
      const status = await getLocalSyncthingStatus();
      return {
        success: true,
        folders: status.folders,
      };
    } catch (err) {
      return { success: false, folders: [], error: err.message };
    }
  });

  // Get local Syncthing device ID (used for backend auto-pair request)
  ipcMain.handle('syncthing-local-device', async () => {
    try {
      const status = await getLocalSyncthingStatus();
      return {
        success: true,
        deviceId: status.deviceId,
        deviceName: `${os.hostname()} Desktop`,
      };
    } catch (err) {
      return { success: false, deviceId: '', error: err.message };
    }
  });

  // Ensure VPS device exists in local Syncthing trusted devices
  ipcMain.handle('syncthing-ensure-remote-device', async (_event, args = {}) => {
    try {
      const result = await ensureLocalSyncthingRemoteDevice(args.deviceId, args.deviceName);
      return { success: true, ...result };
    } catch (err) {
      return { success: false, added: false, error: err.message };
    }
  });


  // Open system folder picker dialog
  ipcMain.handle('dialog-select-folder', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ['openDirectory'],
      title: 'Select folder to sync with Agent',
    });
    if (result.canceled || !result.filePaths.length) {
      return { success: false, canceled: true };
    }
    return { success: true, path: result.filePaths[0] };
  });

  // Add a local folder to Syncthing, shared with VPS
  ipcMain.handle('syncthing-add-folder', async (_event, args = {}) => {
    try {
      const result = await addLocalSyncFolder(args.localPath, args.vpsDeviceId);
      return { success: true, ...result };
    } catch (err) {
      return { success: false, error: err.message };
    }
  });

  // List folders synced with VPS
  ipcMain.handle('syncthing-list-sync-folders', async (_event, args = {}) => {
    try {
      const folders = await listSyncFolders(args.vpsDeviceId);
      return { success: true, folders };
    } catch (err) {
      return { success: false, folders: [], error: err.message };
    }
  });

  // Remove a synced folder
  ipcMain.handle('syncthing-remove-folder', async (_event, args = {}) => {
    try {
      const result = await removeSyncFolder(args.folderId);
      return { success: true, ...result };
    } catch (err) {
      return { success: false, error: err.message };
    }
  });

  // Debug/runtime info for desktop diagnostics
  ipcMain.handle('syncthing-runtime-info', async () => {
    return {
      success: true,
      managed: syncthingRuntime.managed,
      baseUrl: syncthingRuntime.baseUrl,
      configPath: syncthingRuntime.configPath || '',
      processAlive: !!managedSyncthingProcess,
    };
  });

  // ── App config IPC (local persistence for VPS connection) ──

  ipcMain.handle('get-app-config', async () => {
    return readConfig() || {};
  });

  ipcMain.handle('save-app-config', async (_event, config) => {
    const ok = writeConfig(config);
    return { success: ok };
  });

  ipcMain.handle('reload-window', async () => {
    mainWindow.webContents.reload();
    return { success: true };
  });

  // Open external links in default browser instead of new Electron window
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const appUrl = mainWindow.webContents.getURL();
    if (url !== appUrl && !url.startsWith('http://localhost')) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  // In development, load Vite dev server
  if (process.env.NODE_ENV === 'development' || !app.isPackaged) {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'));
  }
}

app.whenReady().then(async () => {
  await initializeSyncthingRuntime();
  createWindow();
});

app.on('before-quit', () => {
  appIsQuitting = true;
  // Fire-and-forget, then force kill fallback
  shutdownManagedSyncthing().catch(() => {});
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
