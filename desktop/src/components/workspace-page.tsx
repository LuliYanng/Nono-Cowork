import { useState, useMemo } from "react";
import { CheckCheck, CheckCircle2 } from "lucide-react";
import {
  NotificationCard,
  NotificationEmpty,
  type Notification,
} from "./notification-card";

// ── Types ──

type WorkspaceTab = "pending" | "done";

interface WorkspacePageProps {
  notifications: Notification[];
  unreadCount: number;
  onNotificationClick?: (notification: Notification) => void;
  onOpenSession?: (notification: Notification) => void;
  onArchive?: (notification: Notification) => void;
  onExecuteAction?: (notificationId: string, actionType: string, deliverableIndex: number) => Promise<boolean>;
  onLoadDetail?: (notification: Notification) => void;
  onMarkAllRead?: () => void;
}

const DONE_STATUSES = new Set(["resolved", "archived", "dismissed"]);

// ── Component ──

export function WorkspacePage({
  notifications,
  unreadCount,
  onNotificationClick,
  onOpenSession,
  onArchive,
  onExecuteAction,
  onLoadDetail,
  onMarkAllRead,
}: WorkspacePageProps) {
  const [tab, setTab] = useState<WorkspaceTab>("pending");

  const { pending, done } = useMemo(() => {
    const p: Notification[] = [];
    const d: Notification[] = [];
    for (const n of notifications) {
      if (DONE_STATUSES.has(n.status)) {
        d.push(n);
      } else {
        p.push(n);
      }
    }
    return { pending: p, done: d };
  }, [notifications]);

  const activeList = tab === "pending" ? pending : done;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Page header */}
      <div className="shrink-0 px-8 pt-6 pb-4">
        <div className="flex items-center justify-between max-w-3xl mx-auto">
          <div>
            <h1 className="text-xl font-semibold text-foreground/85 tracking-tight">
              Workspace
            </h1>
            <p className="text-[13px] text-muted-foreground/50 mt-0.5">
              Agent activity and automated task results
            </p>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2">
            {tab === "pending" && unreadCount > 0 && (
              <span className="text-[12px] text-muted-foreground/40 tabular-nums">
                {unreadCount} unread
              </span>
            )}
            {tab === "pending" && unreadCount > 0 && onMarkAllRead && (
              <button
                onClick={onMarkAllRead}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] text-muted-foreground/50 hover:text-foreground/70 hover:bg-muted/50 transition-colors"
              >
                <CheckCheck size={14} />
                <span>Mark all read</span>
              </button>
            )}
          </div>
        </div>

        {/* ── Tab bar ── */}
        <div className="flex items-center gap-1 mt-4 max-w-3xl mx-auto">
          <button
            onClick={() => setTab("pending")}
            className={`px-4 py-2 rounded-lg text-[13px] font-medium transition-all ${
              tab === "pending"
                ? "bg-foreground/8 text-foreground/80 shadow-sm"
                : "text-muted-foreground/40 hover:text-foreground/60 hover:bg-muted/30"
            }`}
          >
            Pending
          </button>
          <button
            onClick={() => setTab("done")}
            className={`px-4 py-2 rounded-lg text-[13px] font-medium transition-all ${
              tab === "done"
                ? "bg-foreground/8 text-foreground/80 shadow-sm"
                : "text-muted-foreground/40 hover:text-foreground/60 hover:bg-muted/30"
            }`}
          >
            Done
          </button>
        </div>
      </div>

      {/* Notification cards */}
      <div className="flex-1 overflow-y-auto px-8 pb-8">
        <div className="max-w-3xl mx-auto flex flex-col gap-3">
          {activeList.length === 0 ? (
            tab === "pending" ? (
              <NotificationEmpty />
            ) : (
              <div className="flex flex-col items-center justify-center py-20 text-muted-foreground/30">
                <CheckCircle2 size={32} className="mb-3 opacity-50" />
                <p className="text-[14px]">No completed tasks yet</p>
              </div>
            )
          ) : (
            activeList.map((n) => (
              <NotificationCard
                key={n.id}
                notification={n}
                onOpenSession={(notif) => {
                  onNotificationClick?.(notif);
                  onOpenSession?.(notif);
                }}
                onArchive={tab === "pending" ? onArchive : undefined}
                onExecuteAction={tab === "pending" ? onExecuteAction : undefined}
                onLoadDetail={onLoadDetail}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}
