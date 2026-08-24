import { useQuery } from "@tanstack/react-query";
import { hostBeatOf } from "@/entities/match";
import { useHostMatch } from "@/features/host-commands";
import type { HostSocketFactory, SnapshotBody } from "@/shared/api";
import { useAnimationFrame } from "@/shared/lib/use-animation-frame";
import { useServerClock } from "@/shared/lib/use-server-clock";
import { HostBoard } from "@/widgets/host-board";
import { JudgingPanel } from "@/widgets/judging";
import { MatchReset } from "@/widgets/match-reset";
import { MatchSetup } from "@/widgets/match-setup";

export interface MatchPageProps {
  matchId: string;
  socketFactory?: HostSocketFactory;
}

export function MatchPage({ matchId, socketFactory }: MatchPageProps) {
  // H6: the snapshot is fetched only for the stage token, which the socket
  // never carries. Match state comes from the socket and nowhere else.
  const snapshot = useQuery({
    queryKey: ["match-snapshot", matchId],
    queryFn: async (): Promise<SnapshotBody> => {
      const response = await fetch(`/api/matches/${matchId}`);
      if (!response.ok) throw new Error(`snapshot: ${response.status}`);
      return await response.json();
    },
    staleTime: Number.POSITIVE_INFINITY,
  });

  const { frame, refusal, send } = useHostMatch(matchId, socketFactory);
  const now = useServerClock(frame?.server_now ?? null);
  const duel = frame === null ? null : frame.duel;
  useAnimationFrame(duel !== null && !duel.timing.paused);

  if (frame === null) return <p className="p-8 text-stage-muted">Подключение</p>;

  const beat = hostBeatOf(frame);

  return (
    <div className="flex h-full min-h-0 flex-col" data-beat={beat}>
      {refusal && (
        <p className="bg-amber-500/15 px-6 py-2 text-amber-300">
          {refusal.reason ?? refusal.outcome}
          {refusal.message ? ` — ${refusal.message}` : ""}
        </p>
      )}
      <div className="flex justify-end border-white/10 border-b px-6 py-2">
        <MatchReset matchId={matchId} />
      </div>
      <div className="min-h-0 flex-1">
        {beat === "setup" && (
          <MatchSetup
            frame={frame}
            matchId={matchId}
            stageToken={snapshot.data?.stage_token ?? ""}
          />
        )}
        {beat === "board" && (
          <HostBoard
            frame={frame}
            onDeclare={(attacking, defending) =>
              send({
                type: "declare_attack",
                attacking_group: attacking,
                defending_group: defending,
              })
            }
          />
        )}
        {beat === "judging" && duel !== null && (
          <JudgingPanel frame={frame} duel={duel} now={now} send={send} />
        )}
        {beat === "over" && <p className="p-8 font-display text-4xl uppercase">Игра окончена</p>}
      </div>
    </div>
  );
}
