/**
 * EmailDraftAction — Gmail draft review panel embedded in notification cards.
 *
 * Displays a full email draft with fields (to, subject, body) in a
 * Gmail-branded panel. Action buttons (Cancel / Send) are displayed
 * but not wired to backend yet — the callback chain is ready for
 * trivial wiring when the execute-action API is added.
 *
 * Design ref: Gmail compose/draft review interface
 */

import { useState } from "react";
import { Send, CheckCircle2, Loader2, FileEdit } from "lucide-react";
import { Icon } from "@iconify/react";
import { toast } from "sonner";
import type { Deliverable } from "../notification-card";
import type { ActionStatus } from "./types";

// ═══════════════════════════════════════════
//  Props
// ═══════════════════════════════════════════

interface EmailDraftActionProps {
  deliverable: Deliverable;
  isUnread: boolean;
  onExecuteAction?: (actionType: string) => Promise<boolean>;
}

// ═══════════════════════════════════════════
//  Component
// ═══════════════════════════════════════════

export function EmailDraftAction({
  deliverable,
  isUnread,
  onExecuteAction,
}: EmailDraftActionProps) {
  const [status, setStatus] = useState<ActionStatus>("idle");
  const [successMsg, setSuccessMsg] = useState("");
  const meta = (deliverable.metadata || {}) as Record<string, string>;

  const to = meta.to || "";
  const cc = meta.cc || "";
  const subject = meta.subject || "";
  const body = meta.body || meta.body_preview || "";

  const handleSaveDraft = async () => {
    if (!onExecuteAction) return;
    setStatus("loading");
    try {
      const ok = await onExecuteAction("save_draft");
      if (ok) {
        setStatus("success");
        setSuccessMsg("已保存到 Gmail 草稿箱");
        toast.success("已保存到 Gmail 草稿箱");
      } else {
        setStatus("idle");
        toast.error("保存草稿失败，请重试");
      }
    } catch {
      setStatus("idle");
      toast.error("保存草稿失败，请重试");
    }
  };

  const handleSend = async () => {
    if (!onExecuteAction) return;
    setStatus("loading");
    try {
      const ok = await onExecuteAction("send_email");
      if (ok) {
        setStatus("success");
        setSuccessMsg("邮件已发送");
        toast.success("邮件已成功发送");
      } else {
        setStatus("idle");
        toast.error("发送失败，请重试");
      }
    } catch {
      setStatus("idle");
      toast.error("发送失败，请重试");
    }
  };

  return (
    <div
      className={`rounded-xl border overflow-hidden bg-background shadow-[0_2px_8px_-2px_rgba(0,0,0,0.05)] transition-all duration-200 ${
        isUnread ? "border-border/60" : "border-border/30"
      }`}
    >
      {/* ── Header: Official Gmail Look ── */}
      <div className="flex items-center gap-2.5 px-4 py-3 border-b border-border/40">
        <Icon icon="logos:google-gmail" className="w-[15px] h-[15px] drop-shadow-sm" />
        <span className="text-[13px] font-medium text-foreground/85">
          草稿邮件
        </span>
      </div>

      {/* ── Email fields ── */}
      <div className="px-4 pt-3 pb-1 space-y-2">
        {/* To */}
        {to && (
          <FieldRow label="收件人">
            <span className="text-[13px] text-foreground/60 break-all">
              {to}
            </span>
            {cc && (
              <span className="text-[11px] text-muted-foreground/30 ml-2">
                (抄送: {cc})
              </span>
            )}
          </FieldRow>
        )}

        {/* Subject */}
        {subject && (
          <FieldRow label="主题">
            <span
              className={`text-[13px] ${
                isUnread ? "text-foreground/75 font-medium" : "text-foreground/55"
              }`}
            >
              {subject}
            </span>
          </FieldRow>
        )}

        {/* Body */}
        {body && (
          <FieldRow label="内容" alignTop>
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

      {/* ── Action bar ── */}
      <div className="flex items-center justify-end gap-3 px-4 py-3 border-t border-border/40 bg-muted/10">
        {status === "success" ? (
          <div className="flex items-center gap-1.5 text-[13px] font-medium text-emerald-600 dark:text-emerald-500 animate-in fade-in duration-300">
            <CheckCircle2 size={15} />
            <span>{successMsg}</span>
          </div>
        ) : (
          <>
            <button
              onClick={handleSaveDraft}
              disabled={status === "loading"}
              className="flex items-center gap-1.5 px-4 py-2 rounded-full text-[13px] font-medium text-foreground/60 hover:text-foreground hover:bg-muted/60 transition-colors disabled:opacity-50"
            >
              <FileEdit size={13} />
              <span>保存到草稿</span>
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
              <span>发送邮件</span>
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



/** Label-value field row */
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
