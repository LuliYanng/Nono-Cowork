import { useCallback } from "react";
import { useStickToBottomContext } from "use-stick-to-bottom";

/**
 * Prevents stick-to-bottom auto-scroll when expanding collapsibles.
 * When a collapsible opens while the user is at the bottom, this temporarily
 * disables auto-scroll so the expansion naturally pushes content downward
 * instead of causing the viewport to jump up.
 */
export function useScrollAnchor() {
  const { state, stopScroll } = useStickToBottomContext();

  return useCallback(
    (open: boolean) => {
      if (open && state.isAtBottom) {
        stopScroll();
      }
    },
    [state, stopScroll]
  );
}
