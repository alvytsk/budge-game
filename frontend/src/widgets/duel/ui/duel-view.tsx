import type { StageDuelFrame, StageFrame } from "@/shared/api";
import { mediaUrl } from "@/shared/config";
import { TimerPair } from "./timer-pair";

export interface DuelViewProps {
  frame: StageFrame;
  duel: StageDuelFrame;
  now: () => number;
}

/** §9.1 beat 2. The board is gone, the picture is the screen, and the two
 * clocks sit on top. */
export function DuelView({ frame, duel, now }: DuelViewProps) {
  const digest = duel.image_order[duel.index];

  return (
    <div className="flex h-full flex-col gap-6 p-8">
      <TimerPair frame={frame} duel={duel} now={now} />
      <div className="flex min-h-0 flex-1 items-center justify-center">
        {digest !== undefined && (
          <img alt="" src={mediaUrl(digest)} className="max-h-full max-w-full object-contain" />
        )}
      </div>
    </div>
  );
}
