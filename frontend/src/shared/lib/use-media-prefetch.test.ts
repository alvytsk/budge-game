import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useMediaPrefetch } from "./use-media-prefetch";

interface Loader {
  src: string;
  onload: (() => void) | null;
  onerror: (() => void) | null;
}

function stubImage(): Loader[] {
  const loaders: Loader[] = [];
  vi.stubGlobal(
    "Image",
    class {
      src = "";
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor() {
        loaders.push(this as unknown as Loader);
      }
    },
  );
  return loaders;
}

const digest = (letter: string) => letter.repeat(64);

describe("useMediaPrefetch", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches the whole pack, by media URL", () => {
    // §9.1 beat 1 is the loading window: the presenter names the category
    // while the pack warms the browser cache, so beat 2 has no gap. Kills
    // on: doing nothing, which nothing else on the screen would notice —
    // until the projector shows a spinner in front of the room.
    const loaders = stubImage();
    renderHook(() => useMediaPrefetch([digest("a"), digest("b")]));
    expect(loaders.map((loader) => loader.src)).toEqual([
      `/api/media/${digest("a")}`,
      `/api/media/${digest("b")}`,
    ]);
  });

  it("keeps four requests in flight and starts the next as one lands", () => {
    // Kills on: firing the whole pack at once, which on a 40-image pack
    // queues every request behind the browser's connection limit and
    // delivers the first picture last.
    const loaders = stubImage();
    const pack = ["a", "b", "c", "d", "e", "f"].map(digest);
    renderHook(() => useMediaPrefetch(pack));
    expect(loaders).toHaveLength(4);

    loaders[0]?.onload?.();
    expect(loaders).toHaveLength(5);
    loaders[1]?.onerror?.();
    expect(loaders).toHaveLength(6);
    expect(loaders.map((loader) => loader.src)).toEqual(pack.map((d) => `/api/media/${d}`));
  });

  it("stops when the pack goes away", () => {
    // The duel ended: the in-flight lane must not keep pulling the rest
    // of a pack nobody is going to see.
    const loaders = stubImage();
    const { unmount } = renderHook(() => useMediaPrefetch(["a", "b", "c", "d", "e"].map(digest)));
    unmount();
    loaders[0]?.onload?.();
    expect(loaders).toHaveLength(4);
  });

  it("asks for nothing when there is no pack", () => {
    const loaders = stubImage();
    renderHook(() => useMediaPrefetch([]));
    expect(loaders).toHaveLength(0);
  });
});
