const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const { spawn, execSync } = require('child_process');

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

/**
 * Kill any leftover Syncthing processes from prior desktop sessions.
 * On Windows, a crash or force-quit can leave a zombie syncthing.exe
 * that still holds file locks (e.g. on .stignore) but no longer listens
 * on any port — preventing the new embedded instance from starting.
 */
function killZombieSyncthingProcesses(ownExePath) {
  if (process.platform !== 'win32') return;
  try {
    // tasklist gives us the PIDs and image paths of all syncthing.exe
    const raw = execSync(
      'wmic process where "name=\'syncthing.exe\'" get ProcessId,ExecutablePath /format:csv',
      { windowsHide: true, encoding: 'utf8', timeout: 5000 },
    ).trim();
    const lines = raw.split('\n').filter((l) => l.includes('syncthing'));
    const ownResolved = path.resolve(ownExePath);
    for (const line of lines) {
      const parts = line.split(',').map((s) => s.trim());
      // CSV: Node, ExecutablePath, ProcessId
      const exePath = parts[1] || '';
      const pid = parseInt(parts[2], 10);
      if (!pid || pid === process.pid) continue;
      // Only kill processes that match our own managed binary path
      if (path.resolve(exePath) === ownResolved) {
        try {
          process.kill(pid, 'SIGTERM');
          console.info(`[Syncthing] Killed leftover process PID=${pid}`);
        } catch {
          // Already exited or permission denied — ignore.
        }
      }
    }
  } catch {
    // wmic may not be available or timed out — non-fatal.
  }
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

  // Clean up zombie Syncthing processes from prior sessions that may
  // still hold file locks (e.g. .stignore), preventing startup.
  killZombieSyncthingProcesses(exePath);

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
      // Syncthing v2 removed --no-default-folder; passing it makes the
      // process exit with "unknown flag" and we'd hang 20s waiting for an
      // apikey that will never be written. The pre-seeded config.xml above
      // already prevents default-folder creation.
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
// Default Syncthing ignore patterns. Applied as a `.stignore` file at
// the root of any folder we create or adopt, but only if one doesn't
// already exist (we never overwrite the user's custom ignore list).
//
// Philosophy:
//   - Block things that are *regenerable* (node_modules, __pycache__,
//     .venv, dist/, build/) — recreating them on the VPS is wasteful and
//     can cause sync churn with thousands of small files.
//   - Block OS/editor droppings (.DS_Store, Thumbs.db, .vscode cache).
//   - Block secrets by default (.env, *.pem, id_rsa). Users who want to
//     sync config should edit .stignore and remove the specific line.
//   - Do NOT block common source formats — code, docs, configs sync.
//
// If you change this list, mention it in the changelog: users who added
// a folder under an old version keep their older (or missing) .stignore.
// Bump this whenever DEFAULT_STIGNORE content meaningfully changes so
// ensureDefaultStignore() can auto-upgrade folders whose .stignore is
// still the Nono-managed template from an older build.
const STIGNORE_TEMPLATE_VERSION = 2;

const DEFAULT_STIGNORE = [
  `// Managed by Nono CoWork v${STIGNORE_TEMPLATE_VERSION} — edit freely. Lines starting with "//" are comments.`,
  '// This file prevents Syncthing from pushing regenerable or sensitive',
  '// files to the VPS. Delete a line to sync something that\'s currently',
  '// blocked. Keep the header line above intact to let Nono auto-upgrade',
  '// the template; remove it to take full ownership.',
  '',
  '// Version control',
  '.git',
  '.hg',
  '.svn',
  '',
  '// Python',
  '__pycache__',
  '*.pyc',
  '*.pyo',
  // Catch any virtualenv-like directory: .venv, venv, .blog_venv,
  // my-project-venv, virtualenv, .virtualenvs, etc. Users name venvs
  // all kinds of things; a literal list never keeps up.
  '*venv*',
  '*virtualenv*',
  'env',
  '.pytest_cache',
  '.mypy_cache',
  '.ruff_cache',
  '.tox',
  '*.egg-info',
  '',
  '// Node / JS',
  'node_modules',
  '.next',
  '.nuxt',
  '.svelte-kit',
  '.parcel-cache',
  'dist',
  'build',
  '',
  '// Rust / Go / Java',
  'target',
  'out',
  '',
  '// Editors / IDEs',
  '.vscode',
  '.idea',
  '.cursor',
  '*.swp',
  '*.swo',
  '',
  '// OS metadata',
  '.DS_Store',
  'Thumbs.db',
  'desktop.ini',
  '',
  '// Logs & tmp',
  '*.log',
  '*.tmp',
  'tmp',
  'temp',
  '',
  '// Secrets (safety-first default — delete a line to opt in to syncing)',
  '.env',
  '.env.*',
  '*.pem',
  '*.key',
  'id_rsa',
  'id_rsa.pub',
  'id_ed25519',
  'id_ed25519.pub',
  '',
].join('\n');

/**
 * Ensure `<folderPath>/.stignore` exists and is at the current template
 * version. Behavior:
 *   - Missing file → write the default template.
 *   - Exists and starts with the "Managed by Nono CoWork" header →
 *     parse the template version. If older than the current version,
 *     overwrite with the latest template.
 *   - Exists but header is gone → user has taken ownership; leave
 *     completely untouched.
 * Returns true if the file was written (new or upgrade), false otherwise.
 */
function ensureDefaultStignore(folderPath) {
  try {
    const ignorePath = path.join(folderPath, '.stignore');
    if (!fs.existsSync(ignorePath)) {
      // Make sure the folder exists first (caller should already have
      // done this, but be defensive — renderer can call in either order).
      fs.mkdirSync(folderPath, { recursive: true });
      fs.writeFileSync(ignorePath, DEFAULT_STIGNORE, 'utf8');
      return true;
    }

    // On Windows, Syncthing may mark .stignore as Hidden. Node.js
    // fs.writeFileSync throws EPERM when writing to a Hidden file.
    // Clear the Hidden attribute before attempting any write.
    if (process.platform === 'win32') {
      try {
        execSync(`attrib -H "${ignorePath}"`, { windowsHide: true, stdio: 'ignore' });
      } catch {
        // Best-effort: if attrib fails we'll still try the write.
      }
    }

    const existing = fs.readFileSync(ignorePath, 'utf8');
    if (!existing.startsWith('// Managed by Nono CoWork')) {
      // User removed the marker; they own this file now.
      return false;
    }

    // Parse "Managed by Nono CoWork v<N>" from the header. Missing
    // version number means this is a v1 template (the first one shipped
    // with no version marker at all).
    const match = existing.match(/Managed by Nono CoWork v(\d+)/);
    const existingVersion = match ? parseInt(match[1], 10) : 1;
    if (existingVersion >= STIGNORE_TEMPLATE_VERSION) {
      return false;
    }

    fs.writeFileSync(ignorePath, DEFAULT_STIGNORE, 'utf8');
    console.log(
      `[syncthing] upgraded .stignore in ${folderPath}: v${existingVersion} → v${STIGNORE_TEMPLATE_VERSION}`,
    );
    return true;
  } catch (err) {
    // Non-fatal: user can create their own .stignore later.
    console.warn('[syncthing] failed to write/upgrade default .stignore:', err.message);
    return false;
  }
}

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
    // Even when a folder was added under a previous version (no default
    // .stignore), give the user a one-shot fix on re-add. We never
    // clobber an existing ignore file.
    const wroteIgnore = ensureDefaultStignore(alreadySynced.path);
    return {
      folderId: alreadySynced.id,
      folderLabel: alreadySynced.label || alreadySynced.id,
      localPath: alreadySynced.path,
      alreadyExists: true,
      wroteDefaultIgnore: wroteIgnore,
    };
  }

  // Write the default .stignore BEFORE registering the folder with
  // Syncthing so the very first scan already respects it. Syncthing
  // picks up .stignore on scan, not via API.
  const wroteIgnore = ensureDefaultStignore(localPath);

  await syncthingRequest('POST', '/rest/config/folders', {
    id: folderId,
    label: folderLabel,
    path: localPath,
    devices: [{ deviceID: vpsDeviceId }],
    rescanIntervalS: 60,
    fsWatcherEnabled: true,
    fsWatcherDelayS: 1,
  });

  return {
    folderId,
    folderLabel,
    localPath,
    alreadyExists: false,
    wroteDefaultIgnore: wroteIgnore,
  };
}

/**
 * Walk every local Syncthing folder and drop the default .stignore in
 * any that don't have one yet. Used on app startup to retrofit folders
 * created under older Nono CoWork builds that shipped no ignore list.
 *
 * Returns `{ checked, written, written_paths }` for logging. Never
 * throws — best-effort — if Syncthing isn't reachable we just return
 * zeros.
 */
async function ensureIgnoresForAllFolders() {
  try {
    const folders = await syncthingRequest('GET', '/rest/config/folders');
    if (!Array.isArray(folders)) return { checked: 0, written: 0, written_paths: [] };
    let written = 0;
    const written_paths = [];
    for (const f of folders) {
      if (!f.path) continue;
      if (ensureDefaultStignore(f.path)) {
        written += 1;
        written_paths.push(f.path);
      }
    }
    return { checked: folders.length, written, written_paths };
  } catch (err) {
    console.warn('[syncthing] ensureIgnoresForAllFolders failed:', err.message);
    return { checked: 0, written: 0, written_paths: [] };
  }
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

  // Return the user's home directory (used by onboarding to suggest a
  // default workspace at ~/Nono Workspace).
  ipcMain.handle('get-home-dir', async () => {
    return { success: true, path: os.homedir() };
  });

  // Ensure a directory exists (mkdir -p). Used during default-workspace
  // onboarding so the chosen path is guaranteed to exist before Syncthing
  // picks it up.
  ipcMain.handle('ensure-dir', async (_event, args = {}) => {
    try {
      const dirPath = args && args.path;
      if (!dirPath || typeof dirPath !== 'string') {
        return { success: false, error: 'path is required' };
      }
      fs.mkdirSync(dirPath, { recursive: true });
      return { success: true, path: dirPath };
    } catch (err) {
      return { success: false, error: err.message };
    }
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

  // One-shot "retrofit .stignore onto every folder that's missing one".
  // Cheap (one stat per folder); renderer calls it at app mount so
  // workspaces created before we shipped default-ignore support start
  // benefiting immediately.
  ipcMain.handle('syncthing-ensure-ignores', async () => {
    try {
      const result = await ensureIgnoresForAllFolders();
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

  // Reset zoom on every launch and wire our own zoom shortcuts, since the
  // default Electron menu binds Zoom In to `CmdOrCtrl+Plus` (requires Shift+=)
  // and persists zoomLevel across restarts via session preferences.
  mainWindow.webContents.on('did-finish-load', () => {
    mainWindow.webContents.setZoomLevel(0);
  });

  const ZOOM_STEP = 0.5;
  const ZOOM_MIN = -3;
  const ZOOM_MAX = 3;
  mainWindow.webContents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown' || !input.control || input.alt || input.meta) return;
    const key = input.key;
    const wc = mainWindow.webContents;
    // Zoom in: Ctrl+= or Ctrl++ (with or without Shift)
    if (key === '=' || key === '+') {
      wc.setZoomLevel(Math.min(wc.getZoomLevel() + ZOOM_STEP, ZOOM_MAX));
      event.preventDefault();
    } else if (key === '-' || key === '_') {
      wc.setZoomLevel(Math.max(wc.getZoomLevel() - ZOOM_STEP, ZOOM_MIN));
      event.preventDefault();
    } else if (key === '0') {
      wc.setZoomLevel(0);
      event.preventDefault();
    }
  });

  // Open external links in default browser instead of new Electron window
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const appUrl = mainWindow.webContents.getURL();
    // Accept both 127.0.0.1 and localhost so in-app nav works regardless of
    // which form the dev server URL uses. We load from 127.0.0.1 by default
    // (avoids the Windows IPv4/IPv6 mismatch), but stale links might still
    // carry the `localhost` form.
    const isInternalDev =
      url.startsWith('http://127.0.0.1') || url.startsWith('http://localhost');
    if (url !== appUrl && !isInternalDev) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  // In development, load Vite dev server. Explicit IPv4 to match the vite
  // `server.host: '127.0.0.1'` binding — using `localhost` would leave us at
  // the mercy of Windows + Node 17+ DNS resolution order.
  if (process.env.NODE_ENV === 'development' || !app.isPackaged) {
    mainWindow.loadURL('http://127.0.0.1:5173');
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
