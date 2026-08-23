import { useRef } from "react";

/** A getter for "what time is it on the server, right now".
 *
 * §7.3, R2: each frame carries `server_now`; the difference from the
 * browser's own clock at the moment it arrived is the correction, and it
 * is refreshed on every frame rather than measured once. The offset is
 * updated during render — not in an effect — so the first read after a
 * frame is already corrected.
 */
export function useServerClock(serverNow: string | null): () => number {
  const offset = useRef(0);
  const seen = useRef<string | null>(null);

  if (serverNow !== seen.current) {
    seen.current = serverNow;
    const parsed = serverNow === null ? Number.NaN : Date.parse(serverNow);
    offset.current = Number.isFinite(parsed) ? parsed - Date.now() : 0;
  }

  return () => Date.now() + offset.current;
}
