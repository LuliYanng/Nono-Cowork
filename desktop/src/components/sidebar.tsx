import { useState, useEffect, useCallback } from "react";
import {
  Plus,
  PanelLeftClose,
  Settings,
  Trash2,
  ChevronRight,
  LayoutDashboard,
  Repeat,
  Cloud,
  CloudOff,
  RefreshCw,
  Loader2,
  Moon,
  Sun,
  FolderPlus,
  Folder,
  FolderOpen,
  Star,
  MessageSquarePlus,
} from "lucide-react";
import type { SyncState } from "@/hooks/use-sync-status";

// ── Types ──

export type SidebarView = "chat" | "workspace" | "routines";

export interface SessionItem {
  id: string;
  workspace_id?: string | null;
  created_at: number;
  last_active: number;
  message_count: number;
  preview: string;
  is_current: boolean;
}

export interface WorkspaceItem {
  id: string;
  label: string;
  folder_id: string | null;
  is_default: boolean;
  created_at: number;
  last_active: number;
  folder_path?: string | null;
  folder_state?: string | null;
  folder_completion?: number | null;
  session_count: number;
}

interface SidebarProps {
  isOpen: boolean;
  onToggle: () => void;
  onNewChat: () => void;
  sessions: SessionItem[];
  currentSessionId: string | null;
  onSelectSession: (id: string) => void;
  onDeleteSession?: (id: string) => void;
  // Workspaces
  workspaces: WorkspaceItem[];
  activeWorkspaceId: string | null;
  onNewWorkspace: () => void;
  onNewChatInWorkspace: (workspaceId: string) => void;
  onDeleteWorkspace?: (workspaceId: string) => void;
  onRenameWorkspace?: (workspaceId: string) => void;
  // View switching
  activeView: SidebarView;
  onViewChange: (view: SidebarView) => void;
  // Notifications badge
  unreadCount: number;
  // Sync status
  syncState?: SyncState;
  // Settings
  onSettingsOpen?: () => void;
}

// ── Component ──

const SYNC_INDICATOR: Record<
  SyncState,
  { icon: typeof Cloud; label: string; color: string; animate?: boolean }
> = {
  connected:    { icon: Cloud,     label: "Synced",        color: "text-emerald-500" },
  syncing:      { icon: RefreshCw, label: "Syncing...",    color: "text-blue-400",    animate: true },
  disconnected: { icon: CloudOff,  label: "Disconnected",  color: "text-sidebar-foreground/30" },
  loading:      { icon: Loader2,   label: "Connecting...", color: "text-sidebar-foreground/30", animate: true },
};

export function Sidebar({
  isOpen,
  onToggle,
  onNewChat,
  sessions,
  currentSessionId,
  onSelectSession,
  onDeleteSession,
  workspaces,
  activeWorkspaceId,
  onNewWorkspace,
  onNewChatInWorkspace,
  onDeleteWorkspace,
  onRenameWorkspace,
  activeView,
  onViewChange,
  unreadCount,
  syncState = "loading",
  onSettingsOpen,
}: SidebarProps) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [hoveredWsId, setHoveredWsId] = useState<string | null>(null);
  const [expandedWsIds, setExpandedWsIds] = useState<Set<string>>(new Set());

  // Auto-expand the active workspace on mount / when it changes
  useEffect(() => {
    if (activeWorkspaceId) {
      setExpandedWsIds((prev) => {
        if (prev.has(activeWorkspaceId)) return prev;
        const next = new Set(prev);
        next.add(activeWorkspaceId);
        return next;
      });
    }
  }, [activeWorkspaceId]);

  const toggleWorkspace = useCallback((wsId: string) => {
    setExpandedWsIds((prev) => {
      const next = new Set(prev);
      if (next.has(wsId)) next.delete(wsId);
      else next.add(wsId);
      return next;
    });
  }, []);

  // Group sessions by workspace_id (with fallback bucket for unassigned).
  // Sessions without a workspace_id fall into the default workspace.
  const defaultWorkspaceId =
    workspaces.find((w) => w.is_default)?.id || workspaces[0]?.id || null;

  const sessionsByWorkspace = new Map<string, SessionItem[]>();
  for (const s of sessions) {
    const key = s.workspace_id || defaultWorkspaceId || "__none__";
    const arr = sessionsByWorkspace.get(key) || [];
    arr.push(s);
    sessionsByWorkspace.set(key, arr);
  }
  // Sort each group by last_active desc
  for (const arr of sessionsByWorkspace.values()) {
    arr.sort((a, b) => b.last_active - a.last_active);
  }

  // Theme state
  const [isDark, setIsDark] = useState(() => {
    if (typeof window !== "undefined") {
      return document.documentElement.classList.contains("dark");
    }
    return false;
  });

  useEffect(() => {
    const saved = localStorage.getItem("theme");
    if (
      saved === "dark" ||
      (!saved && window.matchMedia("(prefers-color-scheme: dark)").matches)
    ) {
      document.documentElement.classList.add("dark");
      setIsDark(true);
    }
  }, []);

  const toggleTheme = useCallback(() => {
    const next = !isDark;
    setIsDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
  }, [isDark]);

  return (
    <aside
      className={`flex flex-col h-full bg-sidebar text-sidebar-foreground shrink-0 overflow-hidden transition-[width,box-shadow] duration-200 ease-[cubic-bezier(0.4,0,0.2,1)] ${
        isOpen ? "w-[260px] shadow-[4px_0_24px_-4px_rgba(0,0,0,0.15)]" : "w-0"
      }`}
    >
      <div className="flex flex-col h-full min-w-[260px]">
        {/* Drag area + header */}
        <div
          className="flex items-center justify-between px-3 h-11 shrink-0"
          style={{ WebkitAppRegion: "drag" } as React.CSSProperties}
        >
          <span className="text-[13px] font-medium text-sidebar-foreground/60">
            Nono CoWork
          </span>
          <button
            onClick={onToggle}
            className="p-1.5 rounded-md hover:bg-sidebar-accent text-sidebar-foreground/40 hover:text-sidebar-foreground/70 transition-colors"
            style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
            aria-label="Close sidebar"
          >
            <PanelLeftClose size={16} />
          </button>
        </div>

        {/* New Chat button (for the current workspace) */}
        <div className="px-3 py-1">
          <button
            onClick={() => {
              onNewChat();
              onViewChange("chat");
            }}
            className="flex items-center gap-2 w-full px-3 py-2 rounded-lg text-[13px] text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground transition-colors"
            style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
          >
            <Plus size={16} strokeWidth={1.5} />
            <span>New Chat</span>
          </button>
        </div>

        {/* Navigation items */}
        <div
          className="flex flex-col flex-1 overflow-y-auto sidebar-scroll"
          style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
        >
          {/* ── Workspace (notifications page) ── */}
          <div className="px-3">
            <button
              onClick={() => onViewChange("workspace")}
              className={`flex items-center gap-2.5 w-full px-3 py-2 rounded-lg text-[13px] transition-colors ${
                activeView === "workspace"
                  ? "bg-sidebar-accent text-sidebar-foreground/90"
                  : "text-sidebar-foreground/55 hover:bg-sidebar-accent hover:text-sidebar-foreground/80"
              }`}
            >
              <LayoutDashboard size={16} strokeWidth={1.5} />
              <span>Inbox</span>
              {unreadCount > 0 && (
                <span className="ml-auto flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-blue-500/15 text-[11px] font-medium text-blue-500 tabular-nums">
                  {unreadCount}
                </span>
              )}
            </button>
          </div>

          {/* ── Routines ── */}
          <div className="px-3 mt-1">
            <button
              onClick={() => onViewChange("routines")}
              className={`flex items-center gap-2.5 w-full px-3 py-2 rounded-lg text-[13px] transition-colors ${
                activeView === "routines"
                  ? "bg-sidebar-accent text-sidebar-foreground/90"
                  : "text-sidebar-foreground/55 hover:bg-sidebar-accent hover:text-sidebar-foreground/80"
              }`}
            >
              <Repeat size={16} strokeWidth={1.5} />
              <span>Routines</span>
            </button>
          </div>

          {/* ── Workspaces section (one group per workspace) ── */}
          <div className="mt-3 mx-3 flex items-center justify-between px-3 py-1">
            <span className="text-[10px] font-semibold text-sidebar-foreground/40 uppercase tracking-wider">
              Workspaces
            </span>
            <button
              onClick={onNewWorkspace}
              title="New workspace"
              className="p-1 rounded hover:bg-sidebar-accent text-sidebar-foreground/40 hover:text-sidebar-foreground/80 transition-colors"
            >
              <FolderPlus size={13} strokeWidth={1.5} />
            </button>
          </div>

          {workspaces.length === 0 ? (
            <div className="mx-3 px-3 py-2 text-[12px] text-sidebar-foreground/40">
              No workspaces yet.
              <button
                onClick={onNewWorkspace}
                className="block mt-1 text-[12px] text-blue-500 hover:underline"
              >
                Create your first workspace →
              </button>
            </div>
          ) : (
            <div className="flex flex-col">
              {workspaces.map((ws) => {
                const isActive = ws.id === activeWorkspaceId;
                const isExpanded = expandedWsIds.has(ws.id);
                const wsSessions = sessionsByWorkspace.get(ws.id) || [];
                const isHovered = hoveredWsId === ws.id;

                return (
                  <div key={ws.id} className="flex flex-col">
                    {/* Workspace header */}
                    <div
                      className="relative mx-3"
                      onMouseEnter={() => setHoveredWsId(ws.id)}
                      onMouseLeave={() => setHoveredWsId(null)}
                    >
                      <button
                        onClick={() => toggleWorkspace(ws.id)}
                        className={`flex items-center gap-2 w-full px-3 py-1.5 rounded-lg text-[13px] transition-colors ${
                          isActive
                            ? "text-sidebar-foreground/90 font-medium"
                            : "text-sidebar-foreground/70 hover:bg-sidebar-accent/60"
                        }`}
                      >
                        <ChevronRight
                          size={12}
                          className={`text-sidebar-foreground/40 transition-transform duration-150 ${
                            isExpanded ? "rotate-90" : ""
                          }`}
                        />
                        {isExpanded ? (
                          <FolderOpen size={14} strokeWidth={1.5} className="text-sidebar-foreground/60" />
                        ) : (
                          <Folder size={14} strokeWidth={1.5} className="text-sidebar-foreground/60" />
                        )}
                        <span className="truncate flex-1 text-left">{ws.label}</span>
                        {ws.is_default && (
                          <Star
                            size={11}
                            strokeWidth={1.5}
                            className="text-sidebar-foreground/35 shrink-0"
                          />
                        )}
                        {!isHovered && ws.session_count > 0 && (
                          <span className="text-[11px] text-sidebar-foreground/30 tabular-nums shrink-0">
                            {ws.session_count}
                          </span>
                        )}
                      </button>

                      {/* Hover actions: new chat / delete */}
                      {isHovered && (
                        <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-0.5">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              onNewChatInWorkspace(ws.id);
                            }}
                            title="New chat here"
                            className="p-1 rounded-md text-sidebar-foreground/40 hover:text-blue-500 hover:bg-blue-500/10"
                          >
                            <MessageSquarePlus size={12} />
                          </button>
                          {onRenameWorkspace && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                onRenameWorkspace(ws.id);
                              }}
                              title="Rename"
                              className="p-1 rounded-md text-sidebar-foreground/40 hover:text-sidebar-foreground"
                            >
                              <Settings size={12} />
                            </button>
                          )}
                          {onDeleteWorkspace && !ws.is_default && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                onDeleteWorkspace(ws.id);
                              }}
                              title="Delete workspace"
                              className="p-1 rounded-md text-sidebar-foreground/40 hover:text-red-500 hover:bg-red-500/10"
                            >
                              <Trash2 size={12} />
                            </button>
                          )}
                        </div>
                      )}
                    </div>

                    {/* Sessions under this workspace */}
                    {isExpanded && (
                      <div className="flex flex-col gap-0.5 mb-2">
                        {wsSessions.length === 0 ? (
                          <button
                            onClick={() => onNewChatInWorkspace(ws.id)}
                            className="ml-10 mr-3 px-3 py-1.5 text-left text-[12px] text-sidebar-foreground/35 hover:text-blue-500 transition-colors"
                          >
                            No chats yet — start one
                          </button>
                        ) : (
                          wsSessions.map((s) => {
                            const isCurrent = s.id === currentSessionId;
                            return (
                              <div
                                key={s.id}
                                className="relative ml-8 mr-3"
                                onMouseEnter={() => setHoveredId(s.id)}
                                onMouseLeave={() => setHoveredId(null)}
                              >
                                <button
                                  onClick={() => {
                                    if (!isCurrent) onSelectSession(s.id);
                                    onViewChange("chat");
                                  }}
                                  className={`w-full text-left px-3 py-1.5 rounded-lg text-[12.5px] truncate transition-colors pr-8 ${
                                    isCurrent && activeView === "chat"
                                      ? "bg-sidebar-accent text-sidebar-foreground/90"
                                      : "text-sidebar-foreground/55 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground/80"
                                  }`}
                                >
                                  {s.preview || "New conversation"}
                                </button>
                                {onDeleteSession && hoveredId === s.id && !isCurrent && (
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      onDeleteSession(s.id);
                                    }}
                                    className="absolute right-1.5 top-1/2 -translate-y-1/2 p-1 rounded-md text-sidebar-foreground/30 hover:text-red-500 hover:bg-red-500/10 transition-colors"
                                    aria-label="Delete session"
                                  >
                                    <Trash2 size={13} />
                                  </button>
                                )}
                              </div>
                            );
                          })
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer: Sync status + Settings */}
        <div className="mt-auto px-3 py-2 border-t border-sidebar-border shrink-0 space-y-0.5">
          {/* Sync status indicator */}
          {(() => {
            const cfg = SYNC_INDICATOR[syncState];
            const Icon = cfg.icon;
            return (
              <div className={`flex items-center gap-2.5 w-full px-3 py-1.5 rounded-lg text-[12px] ${cfg.color} transition-colors`}>
                <Icon
                  size={14}
                  strokeWidth={1.5}
                  className={cfg.animate ? "animate-spin" : ""}
                />
                <span>{cfg.label}</span>
                {syncState === "connected" && (
                  <span className="ml-auto w-1.5 h-1.5 rounded-full bg-emerald-500" />
                )}
              </div>
            );
          })()}

          {/* Theme toggle */}
          <button
            onClick={toggleTheme}
            className="flex items-center gap-2.5 w-full px-3 py-2 rounded-lg text-[13px] text-sidebar-foreground/40 hover:bg-sidebar-accent hover:text-sidebar-foreground/70 transition-colors"
          >
            {isDark ? <Sun size={15} strokeWidth={1.5} /> : <Moon size={15} strokeWidth={1.5} />}
            <span>{isDark ? "Light Mode" : "Dark Mode"}</span>
          </button>

          {/* Settings */}
          <button
            onClick={onSettingsOpen}
            className="flex items-center gap-2.5 w-full px-3 py-2 rounded-lg text-[13px] text-sidebar-foreground/40 hover:bg-sidebar-accent hover:text-sidebar-foreground/70 transition-colors"
          >
            <Settings size={15} strokeWidth={1.5} />
            <span>Settings</span>
          </button>
        </div>
      </div>
    </aside>
  );
}
