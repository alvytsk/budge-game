import { remainingAt } from "@/entities/duel";
import { colourOf } from "@/entities/match";
import type { StageDuelFrame, StageFrame } from "@/shared/api";
import { cn } from "@/shared/lib/cn";

export function formatClock(ms: number): string {
  const seconds = Math.ceil(Math.max(0, ms) / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

interface OneTimerProps {
  name: string;
  colour: string;
  ms: number;
  active: boolean;
  playerId: string;
}

function OneTimer({ name, colour, ms, active, playerId }: OneTimerProps) {
  return (
    <div
      role="timer"
      data-testid={`timer-${playerId}`}
      data-active={active}
      data-colour={colour}
      className={cn(
        "flex flex-1 flex-col items-center rounded-xl px-8 py-4 transition-opacity",
        active ? "opacity-100" : "opacity-45",
      )}
      style={active ? { background: colour } : undefined}
    >
      <span className="font-sans text-2xl tracking-wide">{name}</span>
      <span className="font-display text-8xl leading-none tabular-nums">{formatClock(ms)}</span>
    </div>
  );
}

export interface TimerPairProps {
  frame: StageFrame;
  duel: StageDuelFrame;
  now: () => number;
}

/** §9.1 beat 2: two clocks on top, the active one in the answering
 * player's colour. Nothing here decides anything — `remainingAt` is
 * arithmetic and `now` is the server's clock. */
export function TimerPair({ frame, duel, now }: TimerPairProps) {
  const at = now();
  return (
    <div className="flex w-full gap-6">
      {[duel.attacker, duel.defender].map((playerId) => (
        <OneTimer
          key={playerId}
          playerId={playerId}
          name={frame.players.find((p) => p.id === playerId)?.name ?? "—"}
          colour={colourOf(frame, playerId)}
          ms={remainingAt(duel.timing, playerId, at)}
          active={duel.timing.answering === playerId && !duel.timing.paused}
        />
      ))}
    </div>
  );
}
