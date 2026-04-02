import { useState } from "react";
import { getDeliverableComponent } from "./deliverables";
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
  EyeOff,
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
}

export interface Notification {
  id: string;
  session_id: string;
  source_type: "trigger" | "schedule" | "syncthing";
  source_id: string;
  source_name: string;
  title: string;
  category: string;
  status: "unread" | "read" | "dismissed" | "archived" | "resolved";
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

// Action icons removed — each specialized component hardcodes its own buttons

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
}: {
  deliverable: Deliverable;
  isUnread: boolean;
}) {
  const DelivIcon = DELIVERABLE_ICONS[deliverable.type] || CheckCircle2;

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
    </div>
  );
}

// EmailDraftCard removed — replaced by actions/EmailDraftAction

// ═══════════════════════════════════════════
//  Deliverable renderer (type router)
// ═══════════════════════════════════════════

function DeliverableCard({
  deliverable,
  isUnread,
  notification,
  onExecuteAction,
}: {
  deliverable: Deliverable;
  isUnread: boolean;
  notification: Notification;
  onExecuteAction?: (actionType: string) => Promise<boolean>;
}) {
  // Registry-based routing — new types only need registration in deliverables/registry.ts
  const SpecializedComponent = getDeliverableComponent(deliverable.type);

  if (SpecializedComponent) {
    return (
      <SpecializedComponent
        deliverable={deliverable}
        isUnread={isUnread}
        notification={notification}
        onExecuteAction={onExecuteAction}
        mode="full"
      />
    );
  }

  // Fallback for unknown types
  return <GenericDeliverableCard deliverable={deliverable} isUnread={isUnread} />;
}

// ═══════════════════════════════════════════
//  Notification Card (main component)
// ═══════════════════════════════════════════

interface NotificationCardProps {
  notification: Notification;
  onOpenSession?: (notification: Notification) => void;
  onArchive?: (notification: Notification) => void;
  onExecuteAction?: (notificationId: string, actionType: string, deliverableIndex: number) => Promise<boolean>;
  onLoadDetail?: (notification: Notification) => void;
}

export function NotificationCard({
  notification,
  onOpenSession,
  onArchive,
  onExecuteAction,
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
              notification={notification}
              onExecuteAction={
                onExecuteAction
                  ? (actionType: string) => onExecuteAction(notification.id, actionType, i)
                  : undefined
              }
            />
          ))}
        </div>
      )}

      {/* ── Footer: continue chat + archive + agent process ── */}
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
            <span>Continue</span>
          </button>
        )}

        <div className="flex-1" />

        {/* Dismiss — universal lifecycle action */}
        {notification.status !== "archived" && notification.status !== "dismissed" && (
          <button
            onClick={() => onArchive?.(notification)}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md text-[11.5px] font-medium text-muted-foreground/30 hover:text-foreground/55 hover:bg-muted/30 transition-colors"
          >
            <EyeOff size={12} />
            <span>Dismiss</span>
          </button>
        )}

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
