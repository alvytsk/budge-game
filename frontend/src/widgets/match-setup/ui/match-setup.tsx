import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { shortfallOf, useCategories, useReadiness } from "@/features/library";
import { useAddPlayer, useAssignSecret, useDeal, useStart } from "@/features/match-assembly";
import type { HostFrame, OutcomeBody } from "@/shared/api";

// §1.1 allows at most four players; six colours give headroom past that
// limit, chosen for separation on a projector rather than on a monitor.
const COLOURS = ["#e4572e", "#2e86e4", "#3fb950", "#d4a017", "#a371f7", "#e45ea0"];

export interface MatchSetupProps {
  frame: HostFrame;
  matchId: string;
  stageToken: string;
}

export function MatchSetup({ frame, matchId, stageToken }: MatchSetupProps) {
  const categories = useCategories();
  const cells = frame.board.width * frame.board.height;
  const readiness = useReadiness(cells, frame.player_count);
  const addPlayer = useAddPlayer();
  const assignSecret = useAssignSecret();
  const deal = useDeal();
  const start = useStart();

  const [name, setName] = useState("");
  const [refusal, setRefusal] = useState<OutcomeBody | null>(null);

  const secrets = categories.data?.filter((row) => row.is_secret && row.is_active) ?? [];
  const missing = readiness.data ? shortfallOf(readiness.data) : [];

  function note(result: OutcomeBody) {
    setRefusal(result.outcome === "accepted" ? null : result);
  }

  return (
    <section className="flex flex-col gap-8 p-8">
      <p className="rounded-lg bg-white/5 p-3 font-mono text-sm text-stage-muted">
        {`Экран сцены: ${window.location.origin}/stage/${stageToken}`}
      </p>

      {readiness.isError ? (
        // §D's whole point is naming a reason instead of a mute screen —
        // a failed request is a reason too, and treating it like a still-
        // loading query (`missing` stays `[]`) would silently put the
        // operator back at the empty picker this task exists to fix.
        <p className="rounded-lg bg-amber-500/15 p-3 text-amber-300">
          Не удалось проверить готовность библиотеки — сервер не ответил.
        </p>
      ) : (
        missing.length > 0 && (
          <p className="flex flex-wrap items-center gap-2 rounded-lg bg-amber-500/15 p-3 text-amber-300">
            <span>{`Библиотеке не хватает: ${missing.join("; ")}.`}</span>
            <Link to="/host/library" className="underline">
              Библиотека
            </Link>
          </p>
        )
      )}

      <div className="flex items-end gap-3">
        <label className="flex flex-col gap-1 text-sm text-stage-muted">
          Имя
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="rounded-lg bg-white/10 px-3 py-2 text-stage-ink"
          />
        </label>
        <button
          type="button"
          onClick={() => {
            const colour = COLOURS[frame.players.length % COLOURS.length] as string;
            void addPlayer
              .mutateAsync({ matchId, player_id: crypto.randomUUID(), name, colour })
              .then(note);
            setName("");
          }}
          className="rounded-lg bg-white/15 px-4 py-2"
        >
          Добавить игрока
        </button>
      </div>

      <ul className="flex flex-col gap-3">
        {frame.players.map((person) => (
          <li key={person.id} className="flex items-center gap-4">
            <span style={{ color: person.colour }} className="w-32 font-display text-xl">
              {person.name}
            </span>
            <label className="flex items-center gap-2 text-sm text-stage-muted">
              {`Секрет ${person.name}`}
              <select
                aria-label={`Секрет ${person.name}`}
                defaultValue=""
                onChange={(event) =>
                  void assignSecret
                    .mutateAsync({ matchId, player_id: person.id, category: event.target.value })
                    .then(note)
                }
                className="rounded bg-white/10 px-2 py-1 text-stage-ink"
              >
                <option value="" disabled>
                  —
                </option>
                {secrets.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.title}
                  </option>
                ))}
              </select>
            </label>
          </li>
        ))}
      </ul>

      {refusal && (
        <p className="rounded-lg bg-amber-500/15 p-3 text-amber-300">
          {refusal.reason ?? refusal.outcome}
          {refusal.message ? ` — ${refusal.message}` : ""}
        </p>
      )}

      <div className="flex gap-3">
        {/* §3.4: the same route, pressed again, IS «перераздать» — so the
            button never goes away, only its label changes. */}
        <button
          type="button"
          onClick={() => void deal.mutateAsync({ matchId }).then(note)}
          className="rounded-lg bg-white/15 px-5 py-3 font-display text-xl uppercase"
        >
          {frame.groups.length > 0 ? "Перераздать" : "Раздать"}
        </button>
        <button
          type="button"
          onClick={() => void start.mutateAsync({ matchId }).then(note)}
          className="rounded-lg bg-emerald-500/25 px-5 py-3 font-display text-xl uppercase"
        >
          Начать
        </button>
      </div>
    </section>
  );
}
