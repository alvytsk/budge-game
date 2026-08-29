import type { ResolutionFrame, StageFrame } from "@/shared/api";

/** How long the capture beat holds the room's attention (R4, §9.1). */
export const CAPTURE_MS = 2600;

export type Beat =
  | { kind: "idle" }
  | { kind: "declared"; groups: [string, string] }
  | { kind: "duel" }
  | { kind: "capture"; resolution: ResolutionFrame }
  | { kind: "endgame"; winner: string | null };

/** Which of §9.1's beats the frame is in.
 *
 * Pure and total (R3): the screen decides nothing, it asks this. The one
 * argument that is not the frame is `sinceFrameMs` — how long the current
 * frame has been on screen — which exists only so the capture beat can
 * end without a second frame arriving (R4).
 *
 * The order below is the priority order, and it is the whole function:
 * an ended match outranks everything, a fresh declaration outranks a
 * capture still animating, and a running duel outranks the board.
 */
export function beatOf(frame: StageFrame, sinceFrameMs: number): Beat {
  if (frame.status === "finished") return { kind: "endgame", winner: frame.winner };

  if (frame.duel !== null) {
    if (frame.duel.phase === "declared") {
      return { kind: "declared", groups: [frame.duel.attacking_group, frame.duel.defending_group] };
    }
    return { kind: "duel" };
  }

  if (frame.resolution !== null && sinceFrameMs < CAPTURE_MS) {
    return { kind: "capture", resolution: frame.resolution };
  }

  return { kind: "idle" };
}
