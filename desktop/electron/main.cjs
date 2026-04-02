const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');

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

  // Open file with system default application
  ipcMain.handle('fs-open-file', async (_event, filePath) => {
    try {
      const errMsg = await shell.openPath(filePath);
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
      const errMsg = await shell.openPath(folderPath);
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
      shell.showItemInFolder(filePath);
      return { success: true };
    } catch (err) {
      return { success: false, error: err.message };
    }
  });

  // Query LOCAL Syncthing API to get local folder paths (zero-config)
  ipcMain.handle('syncthing-local-folders', async () => {
    try {
      const fs = require('fs');
      const http = require('http');
      const os = require('os');

      // Read Syncthing API key from local config.xml
      let configPath;
      if (process.platform === 'win32') {
        configPath = path.join(process.env.LOCALAPPDATA || '', 'Syncthing', 'config.xml');
      } else if (process.platform === 'darwin') {
        configPath = path.join(os.homedir(), 'Library', 'Application Support', 'Syncthing', 'config.xml');
      } else {
        configPath = path.join(os.homedir(), '.local', 'state', 'syncthing', 'config.xml');
      }

      let apiKey = '';
      if (fs.existsSync(configPath)) {
        const xml = fs.readFileSync(configPath, 'utf8');
        const match = xml.match(/<apikey>([^<]+)<\/apikey>/);
        if (match) apiKey = match[1];
      }

      // Query local Syncthing REST API
      const result = await new Promise((resolve, reject) => {
        const req = http.get('http://localhost:8384/rest/config/folders', {
          headers: apiKey ? { 'X-API-Key': apiKey } : {},
          timeout: 3000,
        }, (res) => {
          let data = '';
          res.on('data', (chunk) => { data += chunk; });
          res.on('end', () => {
            try {
              resolve(JSON.parse(data));
            } catch { resolve([]); }
          });
        });
        req.on('error', () => reject(new Error('Local Syncthing not reachable')));
        req.on('timeout', () => { req.destroy(); reject(new Error('Timeout')); });
      });

      // Return simplified folder info: { id, label, path }
      return {
        success: true,
        folders: (Array.isArray(result) ? result : []).map(f => ({
          id: f.id,
          label: f.label || f.id,
          path: f.path,
        })),
      };
    } catch (err) {
      return { success: false, folders: [], error: err.message };
    }
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

app.whenReady().then(createWindow);

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
