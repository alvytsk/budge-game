import { formatClock, remainingAt } from "@/entities/duel";
import { colourOf } from "@/entities/match";
import type { HostCommand, HostDuelFrame, HostFrame } from "@/shared/api";
import { mediaUrl } from "@/shared/config";
import { useHotkeys } from "@/shared/lib/use-hotkeys";

export interface JudgingPanelProps {
  frame: HostFrame;
  duel: HostDuelFrame;
  now: () => number;
  send: (command: HostCommand) => void;
}

/** §9.2's judging layout: two clocks on top, the answer enormous in the
 * middle, a thumbnail and a counter beside it, three buttons underneath.
 * There is deliberately no answer queue and no board here — §9.2 says both
 * add eye movement exactly where its cost is highest. */
export function JudgingPanel({ frame, duel, now, send }: JudgingPanelProps) {
  const running = duel.phase === "running";
  const paused = duel.timing.paused;
  const at = now();

  const pauseOrResume = () => send({ type: paused ? "resume_duel" : "pause_duel" });

  useHotkeys(
    running
      ? {
          Space: () => send({ type: "judge_correct" }),
          KeyP: () => send({ type: "judge_pass" }),
          Escape: pauseOrResume,
          "Ctrl+KeyZ": () => send({ type: "undo_last_judgement" }),
        }
      : { Space: () => send({ type: "start_duel" }) },
    true,
  );

  const digest = duel.image_order[duel.index];

  return (
    <section className="flex h-full flex-col gap-6 p-8">
      <div className="flex gap-6">
        {[duel.attacker, duel.defender].map((playerId) => {
          const colour = colourOf(frame, playerId);
          const active = duel.timing.answering === playerId && !paused;
          return (
            <div
              key={playerId}
              role="timer"
              data-testid={`clock-${playerId}`}
              data-active={active}
              style={active ? { background: colour } : undefined}
              className="flex flex-1 flex-col items-center rounded-xl py-3 data-[active=false]:opacity-45"
            >
              <span>{frame.players.find((person) => person.id === playerId)?.name ?? "—"}</span>
              <span className="font-display text-6xl tabular-nums">
                {formatClock(remainingAt(duel.timing, playerId, at))}
              </span>
            </div>
          );
        })}
      </div>

      <div className="flex min-h-0 flex-1 items-center gap-8">
        <div className="flex w-56 flex-col gap-2">
          {digest !== undefined && (
            <img
              src={mediaUrl(digest)}
              alt={duel.current_answer ?? ""}
              className="w-full rounded-lg object-contain"
            />
          )}
          <span data-testid="picture-count" className="text-center text-stage-muted">
            {`${duel.index + 1} / ${duel.image_count}`}
          </span>
        </div>
        <p
          data-testid="answer"
          className="flex-1 text-center font-display text-[8rem] uppercase leading-none"
        >
          {duel.current_answer ?? "—"}
        </p>
      </div>

      <div className="flex items-center gap-4">
        {running ? (
          <>
            <button
              type="button"
              onClick={() => send({ type: "judge_correct" })}
              className="flex-1 rounded-xl bg-emerald-500/25 py-6 font-display text-3xl uppercase"
            >
              Верно
            </button>
            <button
              type="button"
              onClick={() => send({ type: "judge_pass" })}
              className="flex-1 rounded-xl bg-white/10 py-6 font-display text-3xl uppercase"
            >
              Пас
            </button>
            <button
              type="button"
              onClick={pauseOrResume}
              className="flex-1 rounded-xl bg-amber-500/20 py-6 font-display text-3xl uppercase"
            >
              {paused ? "Продолжить" : "Пауза"}
            </button>
          </>
        ) : (
          <button
            type="button"
            onClick={() => send({ type: "start_duel" })}
            className="flex-1 rounded-xl bg-white/15 py-6 font-display text-3xl uppercase"
          >
            Начать дуэль
          </button>
        )}
        {/* §9.2: «Отмена рядом, но визуально тише». */}
        <button
          type="button"
          data-testid="undo"
          data-quiet="true"
          onClick={() => send({ type: "undo_last_judgement" })}
          className="rounded-lg px-4 py-2 text-sm text-stage-muted underline"
        >
          Отмена
        </button>
      </div>
    </section>
  );
}
