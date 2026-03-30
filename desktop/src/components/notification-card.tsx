import { useState } from "react";
import {
  Mail,
  Clock as ClockIcon,
  FolderSync,
  Webhook,
  Zap,
  ChevronDown,
  MessageSquare,
  FileText,
  FilePlus,
  Send,
  Eye,
  ExternalLink,
  Paperclip,
  FileEdit,
  CheckCircle2,
  Loader2,
  ArrowRight,
} from "lucide-react";

// ═══════════════════════════════════════════
//  Types
// ═══════════════════════════════════════════

export interface DeliverableAction {
  label: string;
  action_type: string;
  primary?: boolean;
}

export interface Deliverable {
  type: string;
  label: string;
  description?: string;
  metadata?: Record<string, unknown>;
  actions?: DeliverableAction[];
}

export interface Notification {
  id: string;
  session_id: string;
  source_type: "trigger" | "schedule" | "syncthing";
  source_id: string;
  source_name: string;
  title: string;
  category: string;
  status: "unread" | "read" | "dismissed";
  summary: string;
  deliverables: Deliverable[];
  agent_provider: string;
  agent_duration_s: number;
  agent_tokens: number;
  user_id: string;
  created_at: string;
  read_at: string | null;
  // Legacy compat
  preview?: string;
}

// ═══════════════════════════════════════════
//  Icons
// ═══════════════════════════════════════════

const SOURCE_ICONS: Record<string, typeof Mail> = {
  trigger: Webhook,
  schedule: ClockIcon,
  syncthing: FolderSync,
};

const CATEGORY_ICONS: Record<string, typeof Mail> = {
  email: Mail,
  code: Zap,
  file: FolderSync,
  report: FileText,
};

const DELIVERABLE_ICONS: Record<string, typeof Paperclip> = {
  file: Paperclip,
  email_draft: FileEdit,
  draft: FileEdit,
  sent_email: Send,
  report: FileText,
  summary: Eye,
  data: FilePlus,
  link: ExternalLink,
};

const ACTION_ICONS: Record<string, typeof Send> = {
  open_draft: Send,
  open_file: ExternalLink,
  send_email: Send,
  link: ExternalLink,
  continue_chat: MessageSquare,
};

function SourceIcon({ notification }: { notification: Notification }) {
  const Icon =
    CATEGORY_ICONS[notification.category] ||
    SOURCE_ICONS[notification.source_type] ||
    Zap;
  return <Icon size={13} strokeWidth={1.5} />;
}

// ═══════════════════════════════════════════
//  Helpers
// ═══════════════════════════════════════════

function relativeTime(isoString: string): string {
  const now = Date.now();
  const then = new Date(isoString).getTime();
  const diff = Math.max(0, now - then);
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(isoString).toLocaleDateString();
}

function cleanSourceName(n: Notification): string {
  if (n.category === "email") return "Gmail";
  if (n.source_type === "schedule") return "Schedule";
  if (n.source_type === "syncthing") return "File Sync";
  return n.source_name.split("_")[0] || n.source_name;
}

function cleanTitle(title: string): string {
  return title.replace(/^[\p{Emoji_Presentation}\p{Emoji}\uFE0F]+\s*/u, "").trim();
}

// ═══════════════════════════════════════════
//  Deliverable Card (generic)
// ═══════════════════════════════════════════

function GenericDeliverableCard({
  deliverable,
  isUnread,
  onAction,
}: {
  deliverable: Deliverable;
  isUnread: boolean;
  onAction?: (action: DeliverableAction) => void;
}) {
  const DelivIcon = DELIVERABLE_ICONS[deliverable.type] || CheckCircle2;
  const actions = deliverable.actions || [];

  return (
    <div
      className={`rounded-lg border px-3 py-2.5 transition-colors ${
        isUnread
          ? "bg-muted/20 border-border/40"
          : "bg-muted/10 border-border/15"
      }`}
    >
      {/* Deliverable header: icon + label + description */}
      <div className="flex items-center gap-2 text-[13px]">
        <span className="shrink-0 text-emerald-600/60">
          <DelivIcon size={14} strokeWidth={1.6} />
        </span>
        <span
          className={`font-medium truncate ${
            isUnread ? "text-foreground/75" : "text-foreground/50"
          }`}
        >
          {deliverable.label}
        </span>
        {deliverable.description && (
          <>
            <span className="text-muted-foreground/20">—</span>
            <span className="text-muted-foreground/40 truncate text-[12px]">
              {deliverable.description}
            </span>
          </>
        )}
      </div>

      {/* Per-deliverable actions */}
      {actions.length > 0 && (
        <div className="flex items-center gap-1.5 mt-2 pl-[22px]">
          {actions.map((action, i) => {
            const ActionIcon = ACTION_ICONS[action.action_type] || ArrowRight;
            return (
              <button
                key={i}
                onClick={() => onAction?.(action)}
                className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-[11.5px] font-medium transition-colors ${
                  action.primary
                    ? "bg-foreground/[0.06] text-foreground/70 hover:bg-foreground/[0.10] hover:text-foreground/90"
                    : "text-muted-foreground/40 hover:text-foreground/60 hover:bg-muted/30"
                }`}
              >
                <ActionIcon size={11} />
                <span>{action.label}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════
//  Email Draft Card (specialized)
// ═══════════════════════════════════════════

function EmailDraftCard({
  deliverable,
  isUnread,
  onAction,
}: {
  deliverable: Deliverable;
  isUnread: boolean;
  onAction?: (action: DeliverableAction) => void;
}) {
  const meta = (deliverable.metadata || {}) as Record<string, string>;
  const actions = deliverable.actions || [];

  return (
    <div
      className={`rounded-lg border px-3 py-2.5 transition-colors ${
        isUnread
          ? "bg-muted/20 border-border/40"
          : "bg-muted/10 border-border/15"
      }`}
    >
      {/* Header */}
      <div className="flex items-center gap-2 text-[13px]">
        <span className="shrink-0 text-blue-500/60">
          <FileEdit size={14} strokeWidth={1.6} />
        </span>
        <span
          className={`font-medium ${isUnread ? "text-foreground/75" : "text-foreground/50"}`}
        >
          {deliverable.label}
        </span>
        {deliverable.description && (
          <>
            <span className="text-muted-foreground/20">—</span>
            <span className="text-muted-foreground/40 text-[12px]">
              {deliverable.description}
            </span>
          </>
        )}
      </div>

      {/* Email metadata */}
      {(meta.to || meta.subject) && (
        <div className="mt-1.5 pl-[22px] space-y-0.5">
          {meta.to && (
            <p className="text-[11.5px] text-muted-foreground/40">
              <span className="text-muted-foreground/25">To:</span> {meta.to}
            </p>
          )}
          {meta.subject && (
            <p className="text-[11.5px] text-muted-foreground/40 truncate">
              <span className="text-muted-foreground/25">Subject:</span> {meta.subject}
            </p>
          )}
          {meta.body_preview && (
            <p
              className="text-[11.5px] text-muted-foreground/35 leading-relaxed mt-1"
              style={{
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }}
            >
              {meta.body_preview}
            </p>
          )}
        </div>
      )}

      {/* Actions */}
      {actions.length > 0 && (
        <div className="flex items-center gap-1.5 mt-2 pl-[22px]">
          {actions.map((action, i) => {
            const ActionIcon = ACTION_ICONS[action.action_type] || ArrowRight;
            return (
              <button
                key={i}
                onClick={() => onAction?.(action)}
                className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-[11.5px] font-medium transition-colors ${
                  action.primary
                    ? "bg-blue-500/10 text-blue-600/80 hover:bg-blue-500/15 hover:text-blue-700"
                    : "text-muted-foreground/40 hover:text-foreground/60 hover:bg-muted/30"
                }`}
              >
                <ActionIcon size={11} />
                <span>{action.label}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════
//  Deliverable renderer (type router)
// ═══════════════════════════════════════════

function DeliverableCard({
  deliverable,
  isUnread,
  onAction,
}: {
  deliverable: Deliverable;
  isUnread: boolean;
  onAction?: (action: DeliverableAction) => void;
}) {
  // Route to specialized component if available
  switch (deliverable.type) {
    case "email_draft":
      return <EmailDraftCard deliverable={deliverable} isUnread={isUnread} onAction={onAction} />;
    default:
      return <GenericDeliverableCard deliverable={deliverable} isUnread={isUnread} onAction={onAction} />;
  }
}

// ═══════════════════════════════════════════
//  Notification Card (main component)
// ═══════════════════════════════════════════

interface NotificationCardProps {
  notification: Notification;
  onOpenSession?: (notification: Notification) => void;
  onDeliverableAction?: (notification: Notification, action: DeliverableAction) => void;
  onLoadDetail?: (notification: Notification) => void;
}

export function NotificationCard({
  notification,
  onOpenSession,
  onDeliverableAction,
  onLoadDetail,
}: NotificationCardProps) {
  const [processExpanded, setProcessExpanded] = useState(false);
  const isUnread = notification.status === "unread";
  const summary = notification.summary || notification.preview || "";
  const deliverables = notification.deliverables || [];

  const handleExpandProcess = () => {
    if (!processExpanded && onLoadDetail) onLoadDetail(notification);
    setProcessExpanded((p) => !p);
  };

  return (
    <div
      className={`rounded-xl border transition-all duration-200 ${
        isUnread
          ? "bg-card border-border/80 shadow-sm"
          : "bg-card/50 border-border/30"
      }`}
    >
      {/* ── Top bar: source + time ── */}
      <div className="flex items-center gap-2 px-4 pt-3 pb-1">
        <div
          className={`flex items-center gap-1.5 text-[11px] ${
            isUnread ? "text-muted-foreground/50" : "text-muted-foreground/35"
          }`}
        >
          <SourceIcon notification={notification} />
          <span>{cleanSourceName(notification)}</span>
        </div>
        <span className="text-[11px] text-muted-foreground/25">·</span>
        <span className="text-[11px] text-muted-foreground/30">
          {relativeTime(notification.created_at)}
        </span>
        {isUnread && (
          <span className="ml-auto w-2 h-2 rounded-full bg-blue-500/80 shrink-0" />
        )}
      </div>

      {/* ── Title ── */}
      <div className="px-4 pt-1 pb-0.5">
        <h3
          className={`text-[14px] leading-snug ${
            isUnread ? "font-semibold text-foreground/90" : "font-medium text-foreground/65"
          }`}
        >
          {cleanTitle(notification.title)}
        </h3>
      </div>

      {/* ── Summary ── */}
      {summary && (
        <div className="px-4 pt-1.5 pb-2">
          <p
            className={`text-[13px] leading-relaxed ${
              isUnread ? "text-foreground/60" : "text-muted-foreground/45"
            }`}
            style={{
              display: "-webkit-box",
              WebkitLineClamp: 4,
              WebkitBoxOrient: "vertical",
              overflow: "hidden",
            }}
          >
            {summary}
          </p>
        </div>
      )}

      {/* ── Deliverables — type-routed rendering ── */}
      {deliverables.length > 0 && (
        <div className="px-4 pb-2 flex flex-col gap-1.5">
          {deliverables.map((d, i) => (
            <DeliverableCard
              key={i}
              deliverable={d}
              isUnread={isUnread}
              onAction={(action) => onDeliverableAction?.(notification, action)}
            />
          ))}
        </div>
      )}

      {/* ── Footer: continue chat + agent process ── */}
      <div className="flex items-center gap-1 px-3 pb-3 border-t border-border/10 pt-2 mx-1">
        {/* Continue chat — always available */}
        {onOpenSession && notification.session_id && (
          <button
            onClick={() => {
              onOpenSession(notification);
            }}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md text-[11.5px] font-medium text-muted-foreground/40 hover:text-foreground/65 hover:bg-muted/30 transition-colors"
          >
            <MessageSquare size={12} />
            <span>继续对话</span>
          </button>
        )}

        <div className="flex-1" />

        {/* Agent process toggle */}
        <button
          onClick={handleExpandProcess}
          className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] text-muted-foreground/30 hover:text-foreground/50 hover:bg-muted/30 transition-colors"
        >
          <ChevronDown
            size={12}
            className={`transition-transform duration-200 ${processExpanded ? "rotate-180" : ""}`}
          />
          <span>Agent</span>
          {notification.agent_provider && (
            <span className="text-muted-foreground/20">
              · {notification.agent_provider}
            </span>
          )}
          {notification.agent_duration_s > 0 && (
            <span className="text-muted-foreground/20">
              · {notification.agent_duration_s}s
            </span>
          )}
        </button>
      </div>

      {/* ── Expanded agent process ── */}
      {processExpanded && (
        <div className="mx-4 mb-4 pt-2 border-t border-border/15">
          <div className="flex items-center gap-2 text-[12px] text-muted-foreground/30 py-2">
            <Loader2 size={12} className="animate-spin" />
            <span>Loading agent activity...</span>
          </div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════
//  Empty state
// ═══════════════════════════════════════════

export function NotificationEmpty() {
  return (
    <div className="flex flex-col items-center justify-center text-muted-foreground/30 gap-3 py-20">
      <div className="p-4 rounded-2xl bg-muted/20">
        <Zap size={32} strokeWidth={1.2} />
      </div>
      <div className="text-center">
        <p className="text-sm font-medium text-foreground/25">No activity yet</p>
        <p className="text-xs text-muted-foreground/20 mt-1.5 max-w-[260px] leading-relaxed">
          When triggers and schedules fire, agent work results will appear here
        </p>
      </div>
    </div>
  );
}
