import { act, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { fakeSocketFactory } from "../../../../testing/fake-socket";
import { hostDuel, hostFrame } from "../../../../testing/host-frames";
import { renderWithQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { MatchPage } from "./match-page";

const MATCH = "33333333-3333-3333-3333-333333333333";

function mount() {
  server.use(
    http.get(`/api/matches/${MATCH}`, () =>
      HttpResponse.json({ frame: hostFrame(), stage_token: "tok-123" }),
    ),
    http.get("/api/library/categories", () => HttpResponse.json([])),
  );
  const { factory, sockets } = fakeSocketFactory();
  renderWithQuery(<MatchPage matchId={MATCH} socketFactory={factory} />);
  act(() => {
    sockets[0]?.open();
  });
  return {
    send: (frame: unknown) =>
      act(() => {
        sockets[0]?.deliver(frame);
      }),
  };
}

describe("MatchPage", () => {
  it("waits before the first frame rather than drawing an empty console", () => {
    mount();
    expect(screen.getByText("Подключение")).toBeInTheDocument();
  });

  it("shows setup before the match has started", () => {
    const { send } = mount();
    send(hostFrame({ status: "setup" }));
    expect(screen.getByRole("button", { name: "Добавить игрока" })).toBeInTheDocument();
  });

  it("shows the board between duels", () => {
    const { send } = mount();
    send(hostFrame({ status: "running", duel: null }));
    expect(screen.getByTestId("group-g1")).toBeInTheDocument();
  });

  it("switches to judging when a duel arrives", () => {
    const { send } = mount();
    send(hostFrame({ status: "running", duel: null }));
    send(hostFrame({ status: "running", duel: hostDuel({ phase: "running" }) }));
    expect(screen.getByTestId("answer")).toHaveTextContent("Титаник");
    expect(screen.queryByTestId("group-g1")).toBeNull();
  });

  it("shows a refusal the operator can read", () => {
    // §6.3, and the reason `useHostMatch` keeps the ack at all.
    const { send } = mount();
    send(hostFrame({ status: "running", duel: null }));
    send({ kind: "ack", correlation_id: "c1", outcome: "rejected", reason: "not_adjacent" });
    expect(screen.getByText(/not_adjacent/)).toBeInTheDocument();
  });

  it("renders the answer, which is the console's alone to hold", () => {
    // The pairing test for the stage plan's R8: `HostDuelFrame` carries
    // `current_answer` and `StageDuelFrame` has no such field, so this is
    // the only surface it can ever reach.
    const { send } = mount();
    send(hostFrame({ status: "running", duel: hostDuel({ phase: "running" }) }));
    expect(screen.getByTestId("answer")).toHaveTextContent("Титаник");
  });
});
