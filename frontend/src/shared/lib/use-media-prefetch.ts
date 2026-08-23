import { useEffect } from "react";
import { mediaUrl } from "../config";

const LANES = 4;

/** Warms the browser cache for a whole image pack. Presentation only: it
 * never reports back, and nothing on the screen waits for it. */
export function useMediaPrefetch(digests: string[]): void {
  const key = digests.join(",");

  useEffect(() => {
    if (key === "") return;
    const queue = key.split(",");
    let cancelled = false;
    let next = 0;

    const pump = (): void => {
      if (cancelled) return;
      const digest = queue[next];
      next += 1;
      if (digest === undefined) return;
      const image = new Image();
      image.onload = pump;
      image.onerror = pump;
      image.src = mediaUrl(digest);
    };

    const lanes = Math.min(LANES, queue.length);
    for (let lane = 0; lane < lanes; lane += 1) pump();

    return () => {
      cancelled = true;
    };
  }, [key]);
}
