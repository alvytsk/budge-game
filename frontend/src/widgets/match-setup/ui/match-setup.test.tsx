import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { hostFrame } from "../../../../testing/host-frames";
import { renderWithQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { MatchSetup } from "./match-setup";

const MATCH = "33333333-3333-3333-3333-333333333333";

function stock() {
  server.use(
    http.get("/api/library/categories", () =>
      HttpResponse.json([
        {
          id: "c1",
          title: "Кино",
          is_secret: false,
          is_active: true,
          version: 1,
          active_image_count: 9,
        },
        {
          id: "s1",
          title: "Тайна",
          is_secret: true,
          is_active: true,
          version: 1,
          active_image_count: 9,
        },
      ]),
    ),
  );
}

describe("MatchSetup", () => {
  it("shows the stage link the operator has to open on the projector", () => {
    stock();
    renderWithQuery(<MatchSetup frame={hostFrame()} matchId={MATCH} stageToken="tok-123" />);
    expect(screen.getByText(/\/stage\/tok-123/)).toBeInTheDocument();
  });

  it("adds a player with the name that was typed and a colour of its own", async () => {
    const sent: { name: string; colour: string }[] = [];
    stock();
    server.use(
      http.post(`/api/matches/${MATCH}/players`, async ({ request }) => {
        sent.push((await request.json()) as { name: string; colour: string });
        return HttpResponse.json({ outcome: "accepted" });
      }),
    );
    renderWithQuery(
      <MatchSetup frame={hostFrame({ players: [] })} matchId={MATCH} stageToken="t" />,
    );
    await userEvent.type(screen.getByLabelText("Имя"), "Аня");
    await userEvent.click(screen.getByRole("button", { name: "Добавить игрока" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]?.name).toBe("Аня");
    expect(sent[0]?.colour).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it("offers only secret categories as a player's secret", async () => {
    // §2.3: a secret is drawn from the secret pool, not from any theme.
    // Kills on: listing every category — the operator would assign an
    // ordinary theme and the deal would behave in a way nothing explains.
    stock();
    renderWithQuery(<MatchSetup frame={hostFrame()} matchId={MATCH} stageToken="t" />);
    // The picker renders before the categories query lands, so wait for
    // an option from the response rather than reading an empty select.
    await screen.findAllByRole("option", { name: "Тайна" });
    const picker = screen.getByLabelText("Секрет Аня");
    const options = Array.from(picker.querySelectorAll("option")).map((node) => node.textContent);
    expect(options).toContain("Тайна");
    expect(options).not.toContain("Кино");
  });

  it("labels the button «Раздать» before a board exists", async () => {
    stock();
    renderWithQuery(
      <MatchSetup frame={hostFrame({ groups: [] })} matchId={MATCH} stageToken="t" />,
    );
    expect(screen.getByRole("button", { name: "Раздать" })).toBeEnabled();
  });

  it("offers a redeal once a board exists, rather than hiding the button", async () => {
    // §3.4. Kills on: disabling or removing it after the first deal — the
    // operator's whole recovery from a bad board is pressing it again.
    stock();
    renderWithQuery(<MatchSetup frame={hostFrame()} matchId={MATCH} stageToken="t" />);
    expect(screen.getByRole("button", { name: "Перераздать" })).toBeEnabled();
  });

  it("shows the reason a deal was refused, and stays usable", async () => {
    // §8: a content gap is an administrator's problem, not a crash.
    stock();
    server.use(
      http.post(`/api/matches/${MATCH}/deal`, () =>
        HttpResponse.json({ outcome: "rejected", reason: "content_unavailable" }, { status: 409 }),
      ),
    );
    renderWithQuery(<MatchSetup frame={hostFrame()} matchId={MATCH} stageToken="t" />);
    await userEvent.click(screen.getByRole("button", { name: "Перераздать" }));
    expect(await screen.findByText(/content_unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Перераздать" })).toBeEnabled();
  });
});
