import { useEffect, useState } from "react";
import type { StageFrame } from "../api/contracts";
import { connectStage, type SocketFactory } from "../api/stage-socket";
import { stageSocketUrl } from "../config/endpoints";

export interface StageFeed {
  frame: StageFrame | null;
  connected: boolean;
}

/** The screen's only state (§7.2): the last frame, and whether the socket
 * is up. A drop does not clear the frame — the room keeps looking at the
 * board it was looking at. */
export function useStageFrame(token: string, factory?: SocketFactory): StageFeed {
  const [feed, setFeed] = useState<StageFeed>({ frame: null, connected: false });

  // `factory` is a test seam handed in once; re-subscribing on its
  // identity would reconnect on every render.
  // biome-ignore lint/correctness/useExhaustiveDependencies: see above
  useEffect(() => {
    setFeed({ frame: null, connected: false });
    return connectStage({
      url: stageSocketUrl(token),
      factory,
      onFrame: (frame) => setFeed({ frame, connected: true }),
      onStatus: (connected) => setFeed((previous) => ({ ...previous, connected })),
    });
  }, [token]);

  return feed;
}
