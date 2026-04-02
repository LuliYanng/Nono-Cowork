/**
 * Shared utilities for deliverable components.
 *
 * - SyncPathResolver: converts VPS paths → local paths
 * - File icon helper: maps file extensions → Lucide icons
 */

import {
  File,
  FileText,
  FileSpreadsheet,
  FileImage,
  FileCode,
  FileArchive,
  FileVideo,
  FileAudio,
  FileType,
  Presentation,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

// ═══════════════════════════════════════════
//  Sync Path Resolver (zero-config)
//
//  Auto-discovers paths from both sides:
//    - VPS side: from backend /api/sync/config
//    - Local side: from Electron IPC → local Syncthing API
//  Matches folders by Syncthing folder ID.
// ═══════════════════════════════════════════

interface FolderMapping {
  id: string;
  remotePath: string;
  localPath: string;
}

class SyncPathResolver {
  private mappings: FolderMapping[] = [];
  private platform = "win32";
  private _initialized = false;

  get initialized() {
    return this._initialized;
  }

  async init(apiBase: string, headers?: Record<string, string>) {
    if (this._initialized) return;

    // 1. Get VPS-side folder config from backend
    let remoteFolders: Array<{ id: string; path: string }> = [];
    try {
      const res = await fetch(`${apiBase}/api/sync/config`, { headers });
      const data = await res.json();
      remoteFolders = data.folders || [];
    } catch {
      // Backend unreachable — can't map paths
    }

    // 2. Get local-side folder config from Electron → local Syncthing
    let localFolders: Array<{ id: string; path: string }> = [];
    if (window.electronAPI?.syncthingLocalFolders) {
      try {
        const result = await window.electronAPI.syncthingLocalFolders();
        if (result.success) {
          localFolders = result.folders;
        }
      } catch {
        // Local Syncthing not running
      }
    }

    // 3. Match by folder ID to build mappings
    for (const remote of remoteFolders) {
      const local = localFolders.find((l) => l.id === remote.id);
      if (local) {
        this.mappings.push({
          id: remote.id,
          remotePath: remote.path.replace(/\/+$/, ""),  // strip trailing /
          localPath: local.path.replace(/[\\/]+$/, ""),  // strip trailing \ or /
        });
      }
    }

    // 4. Fallback: env vars (for non-Electron / development)
    if (this.mappings.length === 0) {
      const remoteRoot = import.meta.env.VITE_SYNC_REMOTE_ROOT || "";
      const localRoot = import.meta.env.VITE_SYNC_LOCAL_ROOT || "";
      if (remoteRoot && localRoot) {
        this.mappings.push({
          id: "env-fallback",
          remotePath: remoteRoot.replace(/\/+$/, ""),
          localPath: localRoot.replace(/[\\/]+$/, ""),
        });
      }
    }

    // 5. Platform
    this.platform = window.electronAPI?.getPlatform?.() || "win32";
    this._initialized = true;

    if (this.mappings.length > 0) {
      console.log(`[SyncPaths] ${this.mappings.length} folder mapping(s) loaded`);
    }
  }

  /**
   * Convert a VPS-side path to a local path.
   *
   * Tries each mapping — first match wins.
   */
  resolve(remotePath: string): string {
    if (this.mappings.length === 0) return remotePath;

    for (const m of this.mappings) {
      // VPS absolute path → strip remote prefix → prepend local prefix
      if (remotePath.startsWith(m.remotePath)) {
        const relative = remotePath.slice(m.remotePath.length);
        const localRel =
          this.platform === "win32"
            ? relative.replace(/\//g, "\\")
            : relative;
        return m.localPath + localRel;
      }
    }

    // Relative path → prepend first mapping's local root
    if (!remotePath.startsWith("/") && !remotePath.match(/^[A-Z]:\\/)) {
      const sep = this.platform === "win32" ? "\\" : "/";
      const localRel =
        this.platform === "win32"
          ? remotePath.replace(/\//g, "\\")
          : remotePath;
      return this.mappings[0].localPath + sep + localRel;
    }

    return remotePath;
  }
}

export const syncPaths = new SyncPathResolver();

// ═══════════════════════════════════════════
//  File Icon Helper
// ═══════════════════════════════════════════

const EXTENSION_ICONS: Record<string, LucideIcon> = {
  // Documents
  pdf: FileText,
  doc: FileText,
  docx: FileText,
  txt: FileText,
  md: FileText,
  rtf: FileText,
  // Spreadsheets
  xls: FileSpreadsheet,
  xlsx: FileSpreadsheet,
  csv: FileSpreadsheet,
  // Presentations
  ppt: Presentation,
  pptx: Presentation,
  // Images
  png: FileImage,
  jpg: FileImage,
  jpeg: FileImage,
  gif: FileImage,
  svg: FileImage,
  webp: FileImage,
  bmp: FileImage,
  ico: FileImage,
  // Code
  js: FileCode,
  ts: FileCode,
  tsx: FileCode,
  jsx: FileCode,
  py: FileCode,
  java: FileCode,
  c: FileCode,
  cpp: FileCode,
  h: FileCode,
  rs: FileCode,
  go: FileCode,
  rb: FileCode,
  php: FileCode,
  html: FileCode,
  css: FileCode,
  json: FileCode,
  yaml: FileCode,
  yml: FileCode,
  xml: FileCode,
  sh: FileCode,
  bat: FileCode,
  ps1: FileCode,
  sql: FileCode,
  // Archives
  zip: FileArchive,
  rar: FileArchive,
  "7z": FileArchive,
  tar: FileArchive,
  gz: FileArchive,
  // Video
  mp4: FileVideo,
  avi: FileVideo,
  mkv: FileVideo,
  mov: FileVideo,
  webm: FileVideo,
  // Audio
  mp3: FileAudio,
  wav: FileAudio,
  flac: FileAudio,
  ogg: FileAudio,
  m4a: FileAudio,
  // Fonts
  ttf: FileType,
  otf: FileType,
  woff: FileType,
  woff2: FileType,
};

export function getFileIcon(extension: string): LucideIcon {
  return EXTENSION_ICONS[extension.toLowerCase()] || File;
}

/**
 * Extract filename from a path (handles both / and \ separators).
 */
export function getFileName(path: string): string {
  return path.replace(/\\/g, "/").split("/").pop() || path;
}

/**
 * Extract file extension (lowercase, without dot).
 */
export function getFileExtension(path: string): string {
  const name = getFileName(path);
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}
