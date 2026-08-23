import type { HostFrame } from "@/shared/api";

export type HostBeat = "setup" | "board" | "judging" | "over";

/** Which of §9.2's screens a frame wants. Pure and total, for the same
 * reason `beatOf` is on the stage side: the console renders what the frame
 * says and decides nothing itself. */
export function hostBeatOf(frame: HostFrame): HostBeat {
  if (frame.status === "finished") return "over";
  if (frame.status === "setup") return "setup";
  return frame.duel === null ? "board" : "judging";
}
