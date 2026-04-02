/**
 * Deliverable Component Registry — type-driven component router.
 *
 * Maps deliverable.type → React component.
 * New types only need: (1) write component, (2) add one line here.
 * No modification to notification-card.tsx or App.tsx needed.
 */

import type { ComponentType } from "react";
import type { Deliverable, Notification } from "../notification-card";
import type { DeliverableMode } from "./types";

// ── Unified props interface for all deliverable components ──

export interface DeliverableComponentProps {
  deliverable: Deliverable;
  isUnread: boolean;
  notification?: Notification;
  onExecuteAction?: (actionType: string) => Promise<boolean>;
  /** "full" for notification cards, "compact" for chat tool results */
  mode?: DeliverableMode;
}

// ── Registry ──

const registry = new Map<string, ComponentType<DeliverableComponentProps>>();

export function registerDeliverable(
  type: string,
  component: ComponentType<DeliverableComponentProps>,
) {
  registry.set(type, component);
}

export function getDeliverableComponent(
  type: string,
): ComponentType<DeliverableComponentProps> | null {
  return registry.get(type) || null;
}

// ── Auto-register all known components ──
// Import order doesn't matter — registration is synchronous at module load.

import { EmailDraftCard } from "./email-draft-card";
import { FileCard } from "./file-card";
import { FileGroupCard } from "./file-group-card";

registerDeliverable("email_draft", EmailDraftCard);
registerDeliverable("file", FileCard);
registerDeliverable("file_group", FileGroupCard);
// registerDeliverable("report", ReportCard);   // FUTURE
// registerDeliverable("link", LinkCard);        // FUTURE
