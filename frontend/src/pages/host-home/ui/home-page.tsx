import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useCreateMatch } from "@/features/match-assembly";
import type { MatchSummaryBody } from "@/shared/api";

export function HomePage() {
  const create = useCreateMatch();
  const [width, setWidth] = useState(4);
  const [height, setHeight] = useState(3);
  const [players, setPlayers] = useState(3);
  // §2.1: a board's sides are at least 3, its cell count is at most 36,
  // and its cells must divide evenly across the players. The button is
  // never disabled — ruling 4 leaves the check to `MatchLifecycle.create`
  // — but the operator gets to see which of the three the current shape
  // breaks before pressing it, rather than learning it from a refusal.
  const cells = width * height;
  const broken =
    width < 3 || height < 3
      ? "Сторона поля — не меньше 3 (§2.1)"
      : cells > 36
        ? `Клеток ${cells}, а больше 36 быть не может`
        : cells % players !== 0
          ? `${cells} не делится на ${players} игроков нацело`
          : null;
  const matches = useQuery({
    queryKey: ["matches"],
    queryFn: async (): Promise<MatchSummaryBody[]> => {
      const response = await fetch("/api/matches");
      if (!response.ok) throw new Error(`matches: ${response.status}`);
      return await response.json();
    },
  });

  return (
    <section className="flex flex-col gap-6 p-8">
      <div className="flex items-center justify-between">
        <h2 className="font-display text-3xl uppercase">Партии</h2>
        <Link to="/host/library" className="text-stage-muted underline">
          Библиотека
        </Link>
      </div>
      <div className="flex items-end gap-3 rounded-xl bg-white/5 p-4">
        {
          // §2.1's minimum side of 3 applies to the board, not the roster —
          // §2.2 still allows a match of two.
          (
            [
              ["Ширина", width, setWidth, 3],
              ["Высота", height, setHeight, 3],
              ["Игроков", players, setPlayers, 1],
            ] as const
          ).map(([label, value, set, min]) => (
            <label key={label} className="flex flex-col gap-1 text-sm text-stage-muted">
              {label}
              <input
                type="number"
                min={min}
                value={value}
                onChange={(event) => set(Number(event.target.value))}
                className="w-20 rounded-lg bg-white/10 px-3 py-2 text-stage-ink"
              />
            </label>
          ))
        }
        {broken && <p className="text-amber-300 text-sm">{broken}</p>}
        <button
          type="button"
          onClick={() =>
            void create.mutateAsync({
              board: { width, height },
              player_count: players,
            })
          }
          className="rounded-lg bg-white/15 px-4 py-2"
        >
          Новая партия
        </button>
      </div>
      {matches.data?.length === 0 && <p className="text-stage-muted">Партий пока нет</p>}
      <ul className="flex flex-col gap-3">
        {matches.data?.map((match) => (
          <li key={match.id} className="rounded-xl bg-white/5 p-4">
            <Link to="/host/match/$matchId" params={{ matchId: match.id }} className="flex gap-4">
              <span className="font-display uppercase">{match.status}</span>
              <span className="flex gap-3">
                {match.players.map((person) => (
                  <span
                    key={person.name}
                    data-testid={`player-${person.name}`}
                    data-eliminated={person.eliminated}
                    style={{ color: person.colour }}
                    className="data-[eliminated=true]:line-through data-[eliminated=true]:opacity-50"
                  >
                    {person.name}
                  </span>
                ))}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
