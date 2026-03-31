import { CheckCheck } from "lucide-react";
import {
  NotificationCard,
  NotificationEmpty,
  type Notification,
} from "./notification-card";

// ── Types ──

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
            {unreadCount > 0 && (
              <span className="text-[12px] text-muted-foreground/40 tabular-nums">
                {unreadCount} unread
              </span>
            )}
            {unreadCount > 0 && onMarkAllRead && (
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
      </div>

      {/* Notification cards */}
      <div className="flex-1 overflow-y-auto px-8 pb-8">
        <div className="max-w-3xl mx-auto flex flex-col gap-3">
          {notifications.length === 0 ? (
            <NotificationEmpty />
          ) : (
            notifications.map((n) => (
              <NotificationCard
                key={n.id}
                notification={n}
                onOpenSession={(notif) => {
                  // Mark as read when opening session
                  onNotificationClick?.(notif);
                  onOpenSession?.(notif);
                }}
                onArchive={onArchive}
                onExecuteAction={onExecuteAction}
                onLoadDetail={onLoadDetail}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}
