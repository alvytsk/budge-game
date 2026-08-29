import { useRef } from "react";
import { type Beat, beatOf } from "@/entities/match";
import type { SocketFactory, StageFrame } from "@/shared/api";
import { useAnimationFrame } from "@/shared/lib/use-animation-frame";
import { useMediaPrefetch } from "@/shared/lib/use-media-prefetch";
import { useServerClock } from "@/shared/lib/use-server-clock";
import { useStageFrame } from "@/shared/lib/use-stage-frame";
import { Board } from "@/widgets/board";
import { DuelView } from "@/widgets/duel";
import { EndgameOverlay, PauseOverlay } from "@/widgets/overlay";

export interface StagePageProps {
  token: string;
  socketFactory?: SocketFactory;
}

function BeatView({ frame, beat, now }: { frame: StageFrame; beat: Beat; now: () => number }) {
  switch (beat.kind) {
    case "endgame":
      return <EndgameOverlay frame={frame} winner={beat.winner} />;
    case "duel":
      return frame.duel === null ? null : <DuelView frame={frame} duel={frame.duel} now={now} />;
    case "declared":
      return <Board frame={frame} highlight={beat.groups} />;
    case "capture":
      return <Board frame={frame} arriving={beat.resolution.absorbed_cells} />;
    case "idle":
      return <Board frame={frame} />;
  }
}

export function StagePage({ token, socketFactory }: StagePageProps) {
  const { frame, connected } = useStageFrame(token, socketFactory);
  const now = useServerClock(frame?.server_now ?? null);

  // When the frame changed, and by the *server's* clock — so the capture
  // window (R4) measures the same interval the server would.
  const arrivedAt = useRef(0);
  const seenSeq = useRef<number | null>(null);
  if (frame !== null && frame.seq !== seenSeq.current) {
    seenSeq.current = frame.seq;
    arrivedAt.current = now();
  }

  const duel = frame === null ? null : frame.duel;
  const running = duel !== null && !duel.timing.paused;
  const capturing = frame !== null && frame.resolution !== null && duel === null;
  // The rAF loop runs while a clock is ticking or a capture is animating,
  // and stops otherwise — an idle board must not repaint sixty times a
  // second for hours (§9.1's between-duel state is the common one).
  useAnimationFrame(running || capturing);

  useMediaPrefetch(duel?.phase === "declared" ? duel.image_order : []);

  if (frame === null) {
    return (
      <main className="flex h-full items-center justify-center font-display text-4xl uppercase text-stage-muted">
        Подключение
      </main>
    );
  }

  const beat = beatOf(frame, now() - arrivedAt.current);
  const paused = duel?.timing.paused === true;

  return (
    <main className="relative h-full w-full" data-beat={beat.kind} data-connected={connected}>
      <BeatView frame={frame} beat={beat} now={now} />
      {paused && <PauseOverlay />}
    </main>
  );
}
