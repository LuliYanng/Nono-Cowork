/**
 * Shared types for notification action components.
 *
 * Action components are interactive panels embedded in notification cards
 * that let the user act on agent deliverables (send email, open file, etc.)
 */

export type ActionStatus = "idle" | "loading" | "success" | "error";

export interface ActionResult {
  success: boolean;
  message?: string;
  error?: string;
}
