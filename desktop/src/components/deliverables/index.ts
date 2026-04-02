/**
 * Deliverables — context-agnostic UI components for agent outputs.
 *
 * Reusable in both notification cards and chat tool results.
 */

// Registry (must be imported to trigger auto-registration)
export { getDeliverableComponent, registerDeliverable } from "./registry";
export type { DeliverableComponentProps } from "./registry";

// Components
export { FileCard } from "./file-card";
export { FileGroupCard } from "./file-group-card";
export { EmailDraftCard } from "./email-draft-card";

// Types
export type {
  DeliverableMode,
  ActionStatus,
  ActionResult,
  FileMetadata,
  FileGroupMetadata,
  EmailDraftMetadata,
  ReportMetadata,
  LinkMetadata,
} from "./types";

// Utils
export { syncPaths, getFileIcon, getFileName, getFileExtension } from "./utils";
