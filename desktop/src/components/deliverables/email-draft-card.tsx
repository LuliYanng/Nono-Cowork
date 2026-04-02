/**
 * EmailDraftCard — Gmail draft review panel.
 *
 * Migrated from actions/email-draft-action.tsx to deliverables/.
 * Now supports dual mode: full (notifications) and compact (chat).
 *
 * Design ref: Gmail compose/draft review interface
 */

import { useState } from "react";
import { Send, CheckCircle2, Loader2, FileEdit, EyeOff } from "lucide-react";
import { Icon } from "@iconify/react";
import { toast } from "sonner";
import type { Deliverable, Notification } from "../notification-card";
import type { ActionStatus, EmailDraftMetadata, DeliverableMode } from "./types";

// ═══════════════════════════════════════════
//  Props
// ═══════════════════════════════════════════

interface EmailDraftCardProps {
  deliverable: Deliverable;
  isUnread: boolean;
  notification?: Notification;
  onExecuteAction?: (actionType: string) => Promise<boolean>;
  mode?: DeliverableMode;
}

// ═══════════════════════════════════════════
//  Component
// ═══════════════════════════════════════════

export function EmailDraftCard({
  deliverable,
  isUnread,
  notification,
  onExecuteAction,
  mode = "full",
}: EmailDraftCardProps) {
  const [status, setStatus] = useState<ActionStatus>("idle");
  const [successMsg, setSuccessMsg] = useState("");
  const meta = (deliverable.metadata || {}) as unknown as EmailDraftMetadata;

  const to = meta.to || "";
  const cc = meta.cc || "";
  const subject = meta.subject || "";
  const body = meta.body || meta.body_preview || "";

  const isResolvedGlobally = notification?.status === "resolved";
  const isArchivedGlobally = notification?.status === "archived" || notification?.status === "dismissed";
  const globalResolvedAction = (notification as unknown as Record<string, string>)?.resolved_action;

  const isSuccessState = status === "success" || isResolvedGlobally;
  const isIgnoredState = isArchivedGlobally && !isSuccessState;

  const displaySuccessMsg =
    successMsg ||
    (globalResolvedAction === "save_draft" ? "Saved to Gmail Drafts"
      : globalResolvedAction === "send_email" ? "Email sent"
      : "Done");

  const handleSaveDraft = async () => {
    if (!onExecuteAction) return;
    setStatus("loading");
    try {
      const ok = await onExecuteAction("save_draft");
      if (ok) {
        setStatus("success");
        setSuccessMsg("Saved to Gmail Drafts");
        toast.success("Saved to Gmail Drafts");
      } else {
        setStatus("idle");
        toast.error("Failed to save draft, please retry");
      }
    } catch {
      setStatus("idle");
      toast.error("Failed to save draft, please retry");
    }
  };

  const handleSend = async () => {
    if (!onExecuteAction) return;
    setStatus("loading");
    try {
      const ok = await onExecuteAction("send_email");
      if (ok) {
        setStatus("success");
        setSuccessMsg("Email sent");
        toast.success("Email sent successfully");
      } else {
        setStatus("idle");
        toast.error("Failed to send, please retry");
      }
    } catch {
      setStatus("idle");
      toast.error("Failed to send, please retry");
    }
  };

  // ── Compact mode: minimal summary for chat ──
  if (mode === "compact") {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border/30 bg-muted/15 px-3 py-2 mt-2">
        <Icon icon="logos:google-gmail" className="w-[13px] h-[13px] shrink-0" />
        <span className="text-[12px] font-medium text-foreground/65 truncate">
          Draft: {subject || "No subject"}
        </span>
        {to && (
          <span className="text-[11px] text-muted-foreground/30 truncate shrink-0">
            → {to}
          </span>
        )}
      </div>
    );
  }

  // ── Full mode: complete Gmail-like panel ──
  return (
    <div
      className={`rounded-xl border overflow-hidden bg-background shadow-[0_2px_8px_-2px_rgba(0,0,0,0.05)] transition-all duration-200 ${
        isUnread ? "border-border/60" : "border-border/30"
      }`}
    >
      {/* Header: Official Gmail Look */}
      <div className="flex items-center gap-2.5 px-4 py-3 border-b border-border/40">
        <Icon icon="logos:google-gmail" className="w-[15px] h-[15px] drop-shadow-sm" />
        <span className="text-[13px] font-medium text-foreground/85">
          Email Draft
        </span>
      </div>

      {/* Email fields */}
      <div className="px-4 pt-3 pb-1 space-y-2">
        {to && (
          <FieldRow label="To">
            <span className="text-[13px] text-foreground/60 break-all">
              {to}
            </span>
            {cc && (
              <span className="text-[11px] text-muted-foreground/30 ml-2">
                (CC: {cc})
              </span>
            )}
          </FieldRow>
        )}

        {subject && (
          <FieldRow label="Subject">
            <span
              className={`text-[13px] ${
                isUnread ? "text-foreground/75 font-medium" : "text-foreground/55"
              }`}
            >
              {subject}
            </span>
          </FieldRow>
        )}

        {body && (
          <FieldRow label="Body" alignTop>
            <div
              className={`text-[13px] leading-[1.75] whitespace-pre-wrap pb-1 ${
                isUnread ? "text-foreground/55" : "text-foreground/40"
              }`}
            >
              {body}
            </div>
          </FieldRow>
        )}
      </div>

      {/* Action bar */}
      <div className="flex items-center justify-end gap-3 px-4 py-3 border-t border-border/40 bg-muted/10">
        {isSuccessState ? (
          <div className="flex items-center gap-1.5 text-[13px] font-medium text-emerald-600 dark:text-emerald-500 animate-in fade-in duration-300">
            <CheckCircle2 size={15} />
            <span>{displaySuccessMsg}</span>
          </div>
        ) : isIgnoredState ? (
          <div className="flex items-center gap-1.5 text-[13px] font-medium text-muted-foreground/50 animate-in fade-in duration-300">
            <EyeOff size={15} />
            <span>Dismissed</span>
          </div>
        ) : (
          <>
            <button
              onClick={handleSaveDraft}
              disabled={status === "loading"}
              className="flex items-center gap-1.5 px-4 py-2 rounded-full text-[13px] font-medium text-foreground/60 hover:text-foreground hover:bg-muted/60 transition-colors disabled:opacity-50"
            >
              <FileEdit size={13} />
              <span>Save Draft</span>
            </button>
            <button
              onClick={handleSend}
              disabled={status === "loading"}
              style={{ backgroundColor: "#0b57d0" }}
              className="flex items-center gap-1.5 px-6 py-2 rounded-full text-[13px] font-medium text-white hover:opacity-90 transition-all disabled:opacity-60 disabled:cursor-not-allowed shadow-sm"
            >
              {status === "loading" ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Send size={13} />
              )}
              <span>Send</span>
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════
//  Sub-components
// ═══════════════════════════════════════════

function FieldRow({
  label,
  alignTop,
  children,
}: {
  label: string;
  alignTop?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={`flex ${alignTop ? "items-start" : "items-baseline"} gap-4`}>
      <span className="text-[12px] text-muted-foreground/35 w-10 text-right shrink-0 leading-[1.75]">
        {label}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
