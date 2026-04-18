import { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import {
  FolderPlus,
  Folder,
  Loader2,
  Check,
  X,
  RefreshCw,
  ChevronRight,
  Settings2,
  Trash2,
  ArrowUpFromLine,
  ArrowDownToLine,
} from "lucide-react";

// ── Status badge folder icon (matches pencil prototype: folder-icons-set) ──

type SyncIconState = "syncing" | "idle" | "error";

function SyncFolderIcon({ state, size = 16 }: { state: SyncIconState; size?: number }) {
  const badgeSize = Math.round(size * 0.625); // 10px at 16px icon
  const iconSize = Math.round(size * 0.5);    // 8px at 16px icon

  const badgeConfig = {
    syncing: { color: "#0B57D0", Icon: RefreshCw, animate: true },
    idle:    { color: "#15A362", Icon: Check,     animate: false },
    error:   { color: "#D14343", Icon: X,         animate: false },
  }[state];

  const { color, Icon, animate } = badgeConfig;

  return (
    <div style={{ position: "relative", width: size, height: size, flexShrink: 0 }}>
      <Folder size={size} style={{ color: "#A8A6A4" }} />
      {/* Status badge — bottom-right overlay */}
      <div
        style={{
          position: "absolute",
          right: -2,
          bottom: -2,
          width: badgeSize,
          height: badgeSize,
          borderRadius: "50%",
          background: "#FFFFFF",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Icon
          size={iconSize}
          style={{ color }}
          className={animate ? "animate-spin" : undefined}
        />
      </div>
    </div>
  );
}

// ── Types ──

interface SyncedFolder {
  id: string;
  label: string;
  localPath: string;
  state: "pending" | "syncing" | "idle" | "error";
  completion: number;
}

interface SyncFileEvent {
  path: string;
  abs_path: string;
  action: "added" | "modified" | "deleted";
  direction: "inbound" | "outbound";
  state: "syncing" | "done" | "error";
  progress: number | null;
  time_ago: string;
  timestamp: number;
  folder_id: string;
}

interface SyncFolderWidgetProps {
  apiBase: string;
  getHeaders: () => Record<string, string>;
  syncState: "connected" | "syncing" | "disconnected" | "loading";
  vpsDeviceId: string;
}

// ── File sync row ──
// A single compact row: direction arrow + filename + progress/time.
// Chronological mixed list (no grouping); the ↑/↓ arrow differentiates direction.

function FileSyncRow({ evt }: { evt: SyncFileEvent }) {
  // Direction arrow:
  //   inbound  = "from you" (user → VPS)   → ↑ (sending up)
  //   outbound = "to you"   (VPS → user)   → ↓ (receiving down)
  const DirectionIcon = evt.direction === "inbound" ? ArrowUpFromLine : ArrowDownToLine;
  const directionColor = evt.direction === "inbound" ? "#A8A6A4" : "#A8A6A4";

  // Progress may be null even when syncing (transfer not yet reported);
  // render ellipsis in that case instead of a bogus percentage.
  const progressLabel =
    evt.state === "syncing"
      ? (evt.progress != null ? `${evt.progress}%` : "…")
      : evt.state === "done"
      ? (evt.time_ago || "Done")
      : "Fail";

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        width: "100%",
        gap: 8,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0, flex: 1 }}>
        <DirectionIcon size={11} style={{ color: directionColor, flexShrink: 0 }} />
        {evt.state === "syncing" && (
          <RefreshCw size={11} className="animate-spin" style={{ color: "#0B57D0", flexShrink: 0 }} />
        )}
        {evt.state === "done" && (
          <Check size={11} style={{ color: "#15A362", flexShrink: 0 }} />
        )}
        {evt.state === "error" && (
          <X size={11} style={{ color: "#D14343", flexShrink: 0 }} />
        )}
        <span
          title={evt.path}
          style={{
            fontSize: 12,
            color: evt.state === "done" || evt.state === "error" ? "#8A8886" : "#333333",
            fontFamily: "Inter, sans-serif",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {evt.path}
        </span>
      </div>
      <span
        style={{
          fontSize: 11,
          fontWeight: evt.state === "syncing" || evt.state === "error" ? 600 : "normal",
          color: evt.state === "syncing" ? "#0B57D0"
            : evt.state === "error" ? "#D14343"
            : "#A8A6A4",
          fontFamily: "Inter, sans-serif",
          flexShrink: 0,
          textAlign: "right",
        }}
      >
        {progressLabel}
      </span>
    </div>
  );
}

// ── Component ──

export function SyncFolderWidget({
  apiBase,
  getHeaders,
  syncState,
  vpsDeviceId,
}: SyncFolderWidgetProps) {
  const [folders, setFolders] = useState<SyncedFolder[]>([]);
  const [syncEvents, setSyncEvents] = useState<SyncFileEvent[]>([]);
  const [totalSyncing, setTotalSyncing] = useState(0);
  const [isAdding, setIsAdding] = useState(false);
  const [showPanel, setShowPanel] = useState(false);
  const [panelPos, setPanelPos] = useState({ bottom: 0, left: 0 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Currently selected folder (first in list, or null)
  const selectedFolder = folders[0] || null;

  // Recalculate panel position whenever it opens
  useEffect(() => {
    if (!showPanel || !buttonRef.current) return;
    const rect = buttonRef.current.getBoundingClientRect();
    setPanelPos({
      bottom: window.innerHeight - rect.top + 6,
      left: rect.left,
    });
  }, [showPanel]);

  // Close panel on outside click
  useEffect(() => {
    if (!showPanel) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      const clickedPanel = panelRef.current?.contains(target);
      const clickedButton = buttonRef.current?.contains(target);
      if (!clickedPanel && !clickedButton) setShowPanel(false);
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

  // Fetch real file-level sync events from backend
  const fetchSyncEvents = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/sync/events?minutes=30&limit=10`, {
        headers: getHeaders(),
      });
      if (!res.ok) return;
      const data = await res.json();
      setSyncEvents(data.events || []);
      setTotalSyncing(data.total_syncing || 0);
    } catch {
      // Silently fail
    }
  }, [apiBase, getHeaders]);

  // Poll every 5s when panel is open or we have syncing folders
  useEffect(() => {
    fetchFolderStatus();
    fetchSyncEvents();
    const hasSyncing = folders.some((f) => f.state === "syncing" || f.state === "pending");
    const interval = setInterval(() => {
      fetchFolderStatus();
      if (showPanel || hasSyncing) fetchSyncEvents();
    }, hasSyncing ? 3000 : 10000);
    return () => clearInterval(interval);
  }, [fetchFolderStatus, fetchSyncEvents, showPanel, folders.some((f) => f.state === "syncing" || f.state === "pending")]);

  // Refresh sync events when panel opens
  useEffect(() => {
    if (showPanel) fetchSyncEvents();
  }, [showPanel, fetchSyncEvents]);

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

  // ── Remove folder flow ──
  const handleRemoveFolder = useCallback(async (folderId: string) => {
    const electron = window.electronAPI;
    if (!electron?.syncthingRemoveFolder) return;

    // Optimistic UI removal
    setFolders((prev) => prev.filter((f) => f.id !== folderId));

    try {
      // Remove from local desktop Syncthing
      await electron.syncthingRemoveFolder({ folderId });
      // Mirror the removal on the VPS side so it doesn't keep re-announcing
      await fetch(`${apiBase}/api/sync/folders/${folderId}`, {
        method: "DELETE",
        headers: getHeaders(),
      });
    } catch {
      // On failure, refresh from server to resync UI state
      fetchFolderStatus();
    }
  }, [apiBase, getHeaders, fetchFolderStatus]);

  const isDisconnected = syncState === "disconnected" || syncState === "loading";
  const hasElectron = !!window.electronAPI?.dialogSelectFolder;

  // Don't show the button at all if not in Electron
  if (!hasElectron) return null;

  // Format path for display
  const displayPath = selectedFolder?.localPath || "";

  // Derive pill icon state from folders + sync events
  const pillIconState: SyncIconState = (() => {
    // Any folder or file with an error → error
    const hasError = folders.some((f) => f.state === "error")
      || syncEvents.some((e) => e.state === "error");
    if (hasError) return "error";
    // Any folder syncing/pending → syncing
    const hasSyncing = folders.some((f) => f.state === "syncing" || f.state === "pending")
      || syncEvents.some((e) => e.state === "syncing")
      || totalSyncing > 0;
    if (hasSyncing) return "syncing";
    return "idle";
  })();

  // Chronological mixed list. Events from backend are already sorted by
  // timestamp desc; cap at 6 rows so the panel stays compact.
  const displayedEvents = syncEvents.slice(0, 6);
  const hasEvents = displayedEvents.length > 0;

  return (
    <>
      {/* folder-selector-pop panel via portal */}
      {showPanel && createPortal(
        <div
          ref={panelRef}
          style={{ position: "fixed", bottom: panelPos.bottom, left: panelPos.left }}
          className="z-[9999]"
        >
          <div
            className="w-[280px] rounded-lg overflow-hidden"
            style={{
              background: "#FFFFFF",
              border: "1px solid #EAE8E6",
              boxShadow: "0 8px 24px rgba(0, 0, 0, 0.09)",
              padding: 12,
              display: "flex",
              flexDirection: "column",
              gap: 10,
            }}
          >
            {/* ── Recent folders section ── */}
            <div style={{ display: "flex", flexDirection: "column", gap: 4, paddingBottom: 8 }}>
              <div style={{ padding: "4px 8px" }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: "#8A8886", fontFamily: "Inter, sans-serif" }}>
                  Recent
                </span>
              </div>

              {/* Folder items */}
              {folders.map((f) => {
                const barColor = f.state === "error" ? "#D14343"
                  : f.state === "idle" ? "#15A362"
                  : "#0B57D0";
                const barPct = Math.max(0, Math.min(100, f.completion ?? 0));
                return (
                  <div
                    key={f.id}
                    className="group transition-colors hover:bg-[#F7F6F5]"
                    style={{
                      padding: "6px 8px",
                      borderRadius: 6,
                      display: "flex",
                      flexDirection: "column",
                      gap: 4,
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        gap: 8,
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0, flex: 1 }}>
                        <Folder size={16} style={{ color: "#333333", flexShrink: 0 }} />
                        <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                          <span
                            style={{
                              fontSize: 13,
                              color: "#333333",
                              fontFamily: "Inter, sans-serif",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {f.label}
                          </span>
                          <span
                            style={{
                              fontSize: 11,
                              color: "#8A8886",
                              fontFamily: "Inter, sans-serif",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {f.localPath}
                          </span>
                        </div>
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                        {f.state !== "idle" && (
                          <span style={{ fontSize: 11, color: "#8A8886", fontFamily: "Inter, sans-serif", minWidth: 32, textAlign: "right" }}>
                            {Math.round(barPct)}%
                          </span>
                        )}
                        {f.id === selectedFolder?.id && (
                          <Check size={14} style={{ color: "#0B57D0", flexShrink: 0 }} />
                        )}
                        <button
                          type="button"
                          aria-label="Remove synced folder"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleRemoveFolder(f.id);
                          }}
                          className="opacity-0 group-hover:opacity-60 hover:!opacity-100 transition-opacity"
                          style={{
                            border: "none",
                            background: "transparent",
                            padding: 2,
                            cursor: "pointer",
                            display: "flex",
                            alignItems: "center",
                          }}
                        >
                          <Trash2 size={14} style={{ color: "#8A8886" }} />
                        </button>
                      </div>
                    </div>
                    {/* Per-folder progress bar — only shown while not idle */}
                    {f.state !== "idle" && (
                      <div style={{ height: 2, borderRadius: 1, background: "#F0EEEC", overflow: "hidden" }}>
                        <div
                          style={{
                            height: "100%",
                            width: `${barPct}%`,
                            background: barColor,
                            transition: "width 0.3s ease",
                          }}
                        />
                      </div>
                    )}
                  </div>
                );
              })}

              {/* No folders yet */}
              {folders.length === 0 && (
                <div style={{ padding: "6px 8px" }}>
                  <span style={{ fontSize: 12, color: "#A8A6A4", fontFamily: "Inter, sans-serif" }}>
                    No synced folders yet
                  </span>
                </div>
              )}

              {/* Choose a different folder */}
              <button
                type="button"
                className="w-full text-left transition-colors hover:bg-[#F7F6F5]"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: 8,
                  borderRadius: 6,
                  border: "none",
                  background: "transparent",
                  cursor: isAdding || isDisconnected ? "not-allowed" : "pointer",
                  opacity: isAdding || isDisconnected ? 0.4 : 1,
                }}
                onClick={handleAddFolder}
                disabled={isAdding || isDisconnected}
              >
                {isAdding ? (
                  <Loader2 size={16} className="animate-spin" style={{ color: "#333333", flexShrink: 0 }} />
                ) : (
                  <FolderPlus size={16} style={{ color: "#333333", flexShrink: 0 }} />
                )}
                <span style={{ fontSize: 13, color: "#333333", fontFamily: "Inter, sans-serif" }}>
                  Choose a different folder
                </span>
              </button>

              {/* Divider */}
              <div style={{ height: 1, width: "100%", background: "#F7F6F5" }} />
            </div>

            {/* ── File Sync Status section ── */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: "#8A8886", fontFamily: "Inter, sans-serif" }}>
                File Sync Status
              </span>
              <Settings2 size={12} style={{ color: "#A8A6A4", cursor: "pointer" }} />
            </div>

            {/* Divider */}
            <div style={{ height: 1, width: "100%", background: "#F7F6F5" }} />

            {/* File sync rows — chronological mixed list, per-row ↑/↓ arrow */}
            {hasEvents ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {displayedEvents.map((evt, i) => (
                  <FileSyncRow key={`${evt.path}-${evt.timestamp}-${i}`} evt={evt} />
                ))}
              </div>
            ) : (
              <div style={{ padding: "4px 0" }}>
                <span style={{ fontSize: 12, color: "#A8A6A4", fontFamily: "Inter, sans-serif" }}>
                  No recent sync activity
                </span>
              </div>
            )}

            {/* Divider + View all (only if there are events) */}
            {hasEvents && (
              <>
                <div style={{ height: 1, width: "100%", background: "#F7F6F5" }} />
                <button
                  type="button"
                  className="w-full transition-colors hover:bg-[#F7F6F5]"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    padding: "4px 0",
                    border: "none",
                    background: "transparent",
                    cursor: "pointer",
                    width: "100%",
                  }}
                >
                  <span style={{ fontSize: 11, fontWeight: 600, color: "#A8A6A4", fontFamily: "Inter, sans-serif" }}>
                    {totalSyncing > 0
                      ? `View all ${totalSyncing} syncing files...`
                      : `View all ${syncEvents.length} recent files...`
                    }
                  </span>
                  <ChevronRight size={12} style={{ color: "#D6D4D2" }} />
                </button>
              </>
            )}
          </div>
        </div>,
        document.body
      )}

      {/* ── Pill trigger button — transparent background, dynamic icon state ── */}
      <button
        type="button"
        ref={buttonRef}
        onClick={selectedFolder ? () => setShowPanel((v) => !v) : handleAddFolder}
        disabled={isAdding || isDisconnected}
        className="flex items-center gap-1.5 transition-colors rounded-md hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer"
        style={{
          padding: "4px 8px",
          border: "none",
          background: "transparent",
        }}
      >
        {isAdding ? (
          <Loader2 size={16} className="animate-spin" style={{ color: "#5A5856" }} />
        ) : selectedFolder ? (
          <SyncFolderIcon state={pillIconState} size={16} />
        ) : (
          <Folder size={16} style={{ color: "#A8A6A4" }} />
        )}
        {selectedFolder ? (
          <>
            <span style={{ fontSize: 12, fontWeight: 600, color: "#5A5856", fontFamily: "Inter, sans-serif" }}>
              {displayPath}
            </span>
            {totalSyncing > 0 && (
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 600,
                  color: "#FFFFFF",
                  background: "#0B57D0",
                  padding: "1px 6px",
                  borderRadius: 10,
                  fontFamily: "Inter, sans-serif",
                  lineHeight: 1.4,
                }}
              >
                {totalSyncing}
              </span>
            )}
          </>
        ) : (
          <span style={{ fontSize: 12, fontWeight: 600, color: "#5A5856", fontFamily: "Inter, sans-serif" }}>
            Sync folder
          </span>
        )}
      </button>
    </>
  );
}
