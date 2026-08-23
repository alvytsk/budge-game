import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import type { MatchSummaryBody } from "@/shared/api";

export function HomePage() {
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
