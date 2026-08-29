import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { renderWithQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { MatchReset } from "./match-reset";

const MATCH = "33333333-3333-3333-3333-333333333333";

function capture(): { sent: { keep_roster: boolean }[] } {
  const sent: { keep_roster: boolean }[] = [];
  server.use(
    http.post(`/api/matches/${MATCH}/reset`, async ({ request }) => {
      sent.push((await request.json()) as { keep_roster: boolean });
      return HttpResponse.json({ outcome: "accepted" });
    }),
  );
  return { sent };
}

describe("MatchReset", () => {
  it("replays the same match with the roster kept", async () => {
    const { sent } = capture();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithQuery(<MatchReset matchId={MATCH} />);
    await userEvent.click(screen.getByRole("button", { name: "Переиграть" }));
    await waitFor(() => expect(sent).toEqual([{ keep_roster: true }]));
  });

  it("wipes the roster on a full reset", async () => {
    const { sent } = capture();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithQuery(<MatchReset matchId={MATCH} />);
    await userEvent.click(screen.getByRole("button", { name: "Сбросить полностью" }));
    await waitFor(() => expect(sent).toEqual([{ keep_roster: false }]));
  });

  it("sends nothing when the operator backs out of the confirmation", async () => {
    // §A.8: the match runs in front of the room, and these buttons sit
    // right next to the ones the host clicks ten times per duel.
    // Kills on: a button without confirmation — one miss-click near
    // «Верно» would wipe the board in front of the room.
    const { sent } = capture();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderWithQuery(<MatchReset matchId={MATCH} />);
    await userEvent.click(screen.getByRole("button", { name: "Сбросить полностью" }));
    expect(sent).toEqual([]);
  });
});
