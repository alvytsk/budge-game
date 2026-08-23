import { createFileRoute } from "@tanstack/react-router";
import { MatchPage } from "@/pages/host-match";

export const Route = createFileRoute("/host/match/$matchId")({ component: RouteComponent });

function RouteComponent() {
  const { matchId } = Route.useParams();
  return <MatchPage matchId={matchId} />;
}
