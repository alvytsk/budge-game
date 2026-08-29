import { useEffect, useRef } from "react";

const TYPING = new Set(["INPUT", "TEXTAREA", "SELECT"]);

function isTyping(): boolean {
  const active = document.activeElement;
  if (!active) return false;
  if (TYPING.has(active.tagName)) return true;
  return active instanceof HTMLElement && active.isContentEditable;
}

/** Window-level keyboard handling for §9.2's judging tempo.
 *
 * Keys are `KeyboardEvent.code` values — the *physical* key — optionally
 * prefixed `Ctrl+`. H4: `event.key` reports the character the current
 * layout produces, and the operator running a Russian-language show has a
 * Russian layout, where P is «з».
 */
export function useHotkeys(map: Record<string, () => void>, active: boolean): void {
  // Callers rebuild the map every render; a ref keeps the listener stable
  // so it is not torn down and rebound on every frame.
  const latest = useRef(map);
  latest.current = map;

  useEffect(() => {
    if (!active) return;
    function onKeyDown(event: KeyboardEvent): void {
      if (isTyping()) return;
      const combo = `${event.ctrlKey || event.metaKey ? "Ctrl+" : ""}${event.code}`;
      const handler = latest.current[combo];
      if (!handler) return;
      // Space scrolls the page and Ctrl+Z reaches the browser's own undo.
      event.preventDefault();
      handler();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [active]);
}
