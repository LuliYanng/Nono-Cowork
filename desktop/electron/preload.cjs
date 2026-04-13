const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // Window controls
  minimize: () => ipcRenderer.send('window-minimize'),
  maximize: () => ipcRenderer.send('window-maximize'),
  close: () => ipcRenderer.send('window-close'),

  // File system operations (for deliverable components)
  openFile: (filePath) => ipcRenderer.invoke('fs-open-file', filePath),
  openFolder: (folderPath) => ipcRenderer.invoke('fs-open-folder', folderPath),
  showInExplorer: (filePath) => ipcRenderer.invoke('fs-show-in-explorer', filePath),

  // Local Syncthing query (for zero-config path mapping)
  syncthingLocalFolders: () => ipcRenderer.invoke('syncthing-local-folders'),
  syncthingLocalDevice: () => ipcRenderer.invoke('syncthing-local-device'),
  syncthingEnsureRemoteDevice: (args) => ipcRenderer.invoke('syncthing-ensure-remote-device', args),
  syncthingEnsureFolders: (args) => ipcRenderer.invoke('syncthing-ensure-folders', args),
  syncthingRuntimeInfo: () => ipcRenderer.invoke('syncthing-runtime-info'),

  // App config (VPS connection persistence)
  getAppConfig: () => ipcRenderer.invoke('get-app-config'),
  saveAppConfig: (config) => ipcRenderer.invoke('save-app-config', config),
  reloadWindow: () => ipcRenderer.invoke('reload-window'),

  // Platform info (for path mapping)
  getPlatform: () => process.platform,
});
