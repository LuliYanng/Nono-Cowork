/**
 * Shared types for Deliverable components.
 *
 * Deliverable components are context-agnostic UI cards that render
 * agent outputs (files, emails, reports, links, etc.) with interactive actions.
 * They can appear in:
 *   - Workspace notification cards (full mode)
 *   - Chat tool call results (compact mode)
 */

// ── Rendering mode ──

export type DeliverableMode = "full" | "compact";

// ── Action status (for buttons with async operations) ──

export type ActionStatus = "idle" | "loading" | "success" | "error";

export interface ActionResult {
  success: boolean;
  message?: string;
  error?: string;
}

// ── Per-type metadata interfaces ──

export interface FileMetadata {
  path: string;
  abs_path?: string;
  size?: string;
  action?: "created" | "modified" | "deleted";
  mime_type?: string;
}

export interface FileGroupMetadata {
  files: Array<{
    path: string;
    action: "created" | "modified" | "deleted";
    size?: string;
  }>;
  summary?: string;
}

export interface EmailDraftMetadata {
  to: string;
  cc?: string;
  subject: string;
  body: string;
  body_preview?: string;
  draft_id?: string;
}

export interface ReportMetadata {
  path: string;
  format?: "md" | "html" | "pdf";
}

export interface LinkMetadata {
  url: string;
  title?: string;
}
