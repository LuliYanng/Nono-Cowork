import { useState, useEffect, useCallback, useRef } from "react";
import {
  FolderSync,
  FolderOpen,
  X,
  Loader2,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

// ── Types ──

interface SyncedFolder {
  id: string;
  label: string;
  localPath: string;
  state: "pending" | "syncing" | "idle" | "error";
  completion: number;
}

interface SyncFolderWidgetProps {
  apiBase: string;
  getHeaders: () => Record<string, string>;
  syncState: "connected" | "syncing" | "disconnected" | "loading";
  vpsDeviceId: string;
}

// ── Helpers ──

function stateIcon(state: SyncedFolder["state"]) {
  switch (state) {
    case "pending":
    case "syncing":
      return <Loader2 size={12} className="animate-spin text-blue-400" />;
    case "idle":
      return <CheckCircle2 size={12} className="text-emerald-500" />;
    case "error":
      return <AlertCircle size={12} className="text-red-400" />;
  }
}

function stateLabel(state: SyncedFolder["state"], completion: number) {
  switch (state) {
    case "pending":
      return "Preparing...";
    case "syncing":
      return `Syncing ${completion.toFixed(0)}%`;
    case "idle":
      return "Synced";
    case "error":
      return "Error";
  }
}

// ── Component ──

export function SyncFolderWidget({
  apiBase,
  getHeaders,
  syncState,
  vpsDeviceId,
}: SyncFolderWidgetProps) {
  const [folders, setFolders] = useState<SyncedFolder[]>([]);
  const [isAdding, setIsAdding] = useState(false);
  const [showPanel, setShowPanel] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  // Close panel on outside click
  useEffect(() => {
    if (!showPanel) return;
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setShowPanel(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showPanel]);

  // Poll folder status from VPS
  const fetchFolderStatus = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/sync/folders`, {
        headers: getHeaders(),
      });
      if (!res.ok) return;
      const data = await res.json();

      // Only show folders with nono- prefix (user-created sync folders)
      const nonoFolders = (data.folders || []).filter(
        (f: any) => f.id.startsWith("nono-")
      );

      setFolders((prev) => {
        return nonoFolders.map((f: any) => {
          const existing = prev.find((p) => p.id === f.id);
          return {
            id: f.id,
            label: f.label,
            localPath: existing?.localPath || f.path,
            state: f.state === "idle" ? "idle" : f.state === "error" ? "error" : "syncing",
            completion: f.completion ?? 100,
          };
        });
      });
    } catch {
      // Silently fail — will retry on next poll
    }
  }, [apiBase, getHeaders]);

  // Poll every 5s when panel is open or we have syncing folders
  useEffect(() => {
    fetchFolderStatus();
    const hasSyncing = folders.some((f) => f.state === "syncing" || f.state === "pending");
    const interval = setInterval(fetchFolderStatus, hasSyncing ? 3000 : 10000);
    return () => clearInterval(interval);
  }, [fetchFolderStatus, folders.some((f) => f.state === "syncing" || f.state === "pending")]);

  // ── Add folder flow ──
  const handleAddFolder = useCallback(async () => {
    const electron = window.electronAPI;
    if (!electron?.dialogSelectFolder || !electron?.syncthingAddFolder) return;
    if (isAdding) return;

    setIsAdding(true);
    try {
      // Step 1: Open system folder picker
      const pick = await electron.dialogSelectFolder();
      if (!pick.success || !pick.path) return;

      const localPath = pick.path;
      const folderName = localPath.split(/[/\\]/).pop() || "Shared";

      // Step 2: Add folder to local Syncthing
      const addResult = await electron.syncthingAddFolder({
        localPath,
        vpsDeviceId,
      });
      if (!addResult.success) return;

      const folderId = addResult.folderId;
      const folderLabel = addResult.folderLabel || folderName;

      // Optimistically add to UI
      if (!addResult.alreadyExists) {
        setFolders((prev) => [
          ...prev,
          {
            id: folderId,
            label: folderLabel,
            localPath,
            state: "pending",
            completion: 0,
          },
        ]);
        setShowPanel(true);
      }

      // Step 3: Tell VPS to create matching receive folder
      const desktopDevice = await electron.syncthingLocalDevice?.();
      await fetch(`${apiBase}/api/sync/folders`, {
        method: "POST",
        headers: { ...getHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({
          folder_id: folderId,
          folder_label: folderLabel,
          desktop_device_id: desktopDevice?.deviceId || "",
        }),
      });

      // Start polling for status
      fetchFolderStatus();
    } catch {
      // Silent — best effort
    } finally {
      setIsAdding(false);
    }
  }, [apiBase, getHeaders, vpsDeviceId, isAdding, fetchFolderStatus]);

  // ── Remove folder ──
  const handleRemoveFolder = useCallback(
    async (folderId: string) => {
      const electron = window.electronAPI;

      // Remove from VPS
      try {
        await fetch(`${apiBase}/api/sync/folders/${folderId}`, {
          method: "DELETE",
          headers: getHeaders(),
        });
      } catch {}

      // Remove from local Syncthing
      try {
        await electron?.syncthingRemoveFolder?.({ folderId });
      } catch {}

      setFolders((prev) => prev.filter((f) => f.id !== folderId));
    },
    [apiBase, getHeaders]
  );

  const isDisconnected = syncState === "disconnected" || syncState === "loading";
  const hasElectron = !!window.electronAPI?.dialogSelectFolder;

  // Don't show the button at all if not in Electron
  if (!hasElectron) return null;

  return (
    <div className="relative" ref={panelRef}>
      {/* Sync folder panel — above the button */}
      {showPanel && folders.length > 0 && (
        <div className="absolute bottom-full left-0 mb-1.5 w-72 bg-background border border-border/50 rounded-lg shadow-xl overflow-hidden z-50">
          <div className="px-3 py-2 border-b border-border/30 flex items-center justify-between">
            <span className="text-[11px] font-medium text-foreground/70">
              Synced Folders
            </span>
            <button
              onClick={() => setShowPanel(false)}
              className="p-0.5 rounded hover:bg-muted text-muted-foreground/50 hover:text-foreground transition-colors"
            >
              <X size={12} />
            </button>
          </div>
          <div className="max-h-48 overflow-y-auto">
            {folders.map((f) => (
              <div
                key={f.id}
                className="px-3 py-2 flex items-center gap-2 hover:bg-muted/30 group"
              >
                <FolderOpen size={14} className="text-muted-foreground/60 shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="text-[12px] text-foreground truncate">
                    {f.label}
                  </div>
                  <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground/60">
                    {stateIcon(f.state)}
                    <span>{stateLabel(f.state, f.completion)}</span>
                  </div>
                </div>
                <button
                  onClick={() => handleRemoveFolder(f.id)}
                  className="p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-red-500/10 text-muted-foreground/40 hover:text-red-400 transition-all"
                  title="Stop syncing"
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
          {/* Add more button inside panel */}
          <button
            onClick={handleAddFolder}
            disabled={isAdding || isDisconnected}
            className="w-full px-3 py-2 border-t border-border/30 text-[11px] text-muted-foreground/60 hover:text-foreground hover:bg-muted/30 transition-colors flex items-center gap-2 disabled:opacity-40"
          >
            {isAdding ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <FolderSync size={12} />
            )}
            Add another folder
          </button>
        </div>
      )}

      {/* Main button */}
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            onClick={folders.length > 0 ? () => setShowPanel((v) => !v) : handleAddFolder}
            disabled={isAdding || isDisconnected}
            className="relative flex items-center justify-center h-6 w-6 rounded-md text-muted-foreground/50 hover:text-foreground hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed transition-colors cursor-pointer"
          >
            {isAdding ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <FolderSync size={14} />
            )}
            {/* Badge showing folder count */}
            {folders.length > 0 && (
              <span className="absolute -top-1 -right-1 flex items-center justify-center h-3.5 min-w-[14px] rounded-full bg-blue-500 text-[8px] text-white font-medium px-0.5">
                {folders.length}
              </span>
            )}
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs">
          {isDisconnected
            ? "Sync unavailable — not connected"
            : folders.length > 0
              ? "Manage synced folders"
              : "Sync a folder with Agent"}
        </TooltipContent>
      </Tooltip>
    </div>
  );
}
