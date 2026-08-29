import { createFileRoute } from "@tanstack/react-router";
import { StagePage } from "@/pages/stage";

export const Route = createFileRoute("/stage/$token")({
  component: RouteComponent,
});

function RouteComponent() {
  const { token } = Route.useParams();
  return <StagePage token={token} />;
}
