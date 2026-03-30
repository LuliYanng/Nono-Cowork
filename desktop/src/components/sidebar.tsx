import { useState } from "react";
import {
  Plus,
  PanelLeftClose,
  Settings,
  Clock,
  Trash2,
  ChevronRight,
  Zap,
  Repeat,
} from "lucide-react";

// ── Types ──

export type SidebarView = "chat" | "workspace" | "routines";

export interface SessionItem {
  id: string;
  created_at: number;
  last_active: number;
  message_count: number;
  preview: string;
  is_current: boolean;
}

interface SidebarProps {
  isOpen: boolean;
  onToggle: () => void;
  onNewChat: () => void;
  sessions: SessionItem[];
  onSelectSession: (id: string) => void;
  onDeleteSession?: (id: string) => void;
  // View switching
  activeView: SidebarView;
  onViewChange: (view: SidebarView) => void;
  // Notifications badge
  unreadCount: number;
}

// ── Date grouping ──

function groupByDate(sessions: SessionItem[]): [string, SessionItem[]][] {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const todayMs = today.getTime();
  const yesterdayMs = todayMs - 86_400_000;
  const weekMs = todayMs - 7 * 86_400_000;
  const monthMs = todayMs - 30 * 86_400_000;

  const groups = new Map<string, SessionItem[]>();
  const order = ["Today", "Yesterday", "Previous 7 Days", "Previous 30 Days", "Older"];

  for (const s of sessions) {
    const t = s.last_active * 1000;
    let group: string;
    if (t >= todayMs) group = "Today";
    else if (t >= yesterdayMs) group = "Yesterday";
    else if (t >= weekMs) group = "Previous 7 Days";
    else if (t >= monthMs) group = "Previous 30 Days";
    else group = "Older";

    if (!groups.has(group)) groups.set(group, []);
    groups.get(group)!.push(s);
  }

  return order.filter((g) => groups.has(g)).map((g) => [g, groups.get(g)!]);
}

// ── Component ──

export function Sidebar({
  isOpen,
  onToggle,
  onNewChat,
  sessions,
  onSelectSession,
  onDeleteSession,
  activeView,
  onViewChange,
  unreadCount,
}: SidebarProps) {
  const [historyExpanded, setHistoryExpanded] = useState(false);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const grouped = groupByDate(sessions);

  return (
    <aside
      className={`flex flex-col h-full bg-sidebar text-sidebar-foreground shrink-0 border-r overflow-hidden transition-[width] duration-200 ease-[cubic-bezier(0.4,0,0.2,1)] ${
        isOpen ? "w-[260px] border-sidebar-border" : "w-0 border-transparent"
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

        {/* New Chat button */}
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
          className="flex flex-col flex-1 overflow-y-auto"
          style={{ WebkitAppRegion: "no-drag", scrollbarGutter: "stable" } as React.CSSProperties}
        >
          {/* ── Workspace — page navigation ── */}
          <div className="px-3">
            <button
              onClick={() => onViewChange("workspace")}
              className={`flex items-center gap-2.5 w-full px-3 py-2 rounded-lg text-[13px] transition-colors ${
                activeView === "workspace"
                  ? "bg-sidebar-accent text-sidebar-foreground/90"
                  : "text-sidebar-foreground/55 hover:bg-sidebar-accent hover:text-sidebar-foreground/80"
              }`}
            >
              <Zap size={16} strokeWidth={1.5} />
              <span>Workspace</span>
              {unreadCount > 0 && (
                <span className="ml-auto flex items-center justify-center min-w-[18px] h-[18px] px-1 rounded-full bg-blue-500/15 text-[11px] font-medium text-blue-500 tabular-nums">
                  {unreadCount}
                </span>
              )}
            </button>
          </div>

          {/* ── Routines — page navigation ── */}
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

          {/* ── History — collapsible ── */}
          <div className="flex flex-col min-h-0">
            <button
              onClick={() => setHistoryExpanded((p) => !p)}
              className="flex items-center gap-2.5 mx-3 px-3 py-2 rounded-lg text-[13px] text-sidebar-foreground/55 hover:bg-sidebar-accent hover:text-sidebar-foreground/80 transition-colors"
            >
              <Clock size={16} strokeWidth={1.5} />
              <span>History</span>
              {sessions.length > 0 && (
                <span className="ml-auto text-[11px] text-sidebar-foreground/30 tabular-nums">
                  {sessions.length}
                </span>
              )}
              <ChevronRight
                size={14}
                className={`text-sidebar-foreground/30 transition-transform duration-200 ${
                  historyExpanded ? "rotate-90" : ""
                }`}
              />
            </button>

            {/* Expandable session list */}
            {historyExpanded && (
              <div className="flex flex-col mb-1.5 mt-0.5">
                {sessions.length === 0 ? (
                  <div className="flex flex-col text-sidebar-foreground/30 gap-1 py-4 ml-6">
                    <p className="text-[12px]">No history</p>
                  </div>
                ) : (
                  grouped.map(([group, items]) => (
                    <div key={group} className="mb-2 last:mb-0 flex flex-col gap-0.5">
                      <div className="ml-6 py-1 text-[10px] font-semibold text-sidebar-foreground/35 uppercase tracking-wider">
                        {group}
                      </div>
                      {items.map((s) => (
                        <div
                          key={s.id}
                          className="relative ml-5 mr-3"
                          onMouseEnter={() => setHoveredId(s.id)}
                          onMouseLeave={() => setHoveredId(null)}
                        >
                          <button
                            onClick={() => {
                              if (!s.is_current) onSelectSession(s.id);
                              onViewChange("chat");
                            }}
                            className={`w-full text-left px-3 py-1.5 rounded-lg text-[13px] truncate transition-colors pr-8 ${
                              s.is_current && activeView === "chat"
                                ? "bg-sidebar-accent text-sidebar-foreground/90"
                                : "text-sidebar-foreground/55 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground/80"
                            }`}
                          >
                            {s.preview || "New conversation"}
                          </button>
                          {onDeleteSession && hoveredId === s.id && !s.is_current && (
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
                      ))}
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>

        {/* Settings */}
        <div className="mt-auto px-3 py-3 border-t border-sidebar-border shrink-0">
          <button className="flex items-center gap-2.5 w-full px-3 py-2 rounded-lg text-[13px] text-sidebar-foreground/40 hover:bg-sidebar-accent hover:text-sidebar-foreground/70 transition-colors">
            <Settings size={15} strokeWidth={1.5} />
            <span>Settings</span>
          </button>
        </div>
      </div>
    </aside>
  );
}
