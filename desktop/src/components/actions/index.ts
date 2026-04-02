/**
 * Backward-compatible re-exports from deliverables/.
 *
 * Existing imports from "./actions" will continue to work.
 * New code should import from "./deliverables" directly.
 */

// Re-export the old name as alias for backward compat
export { EmailDraftCard as EmailDraftAction } from "../deliverables/email-draft-card";
export type { ActionStatus, ActionResult } from "../deliverables/types";
