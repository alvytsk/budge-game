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
    // `MatchSetup` always asks readiness now (§C); tests that only care
    // about the rest of the screen get a plain "ready" answer here, and
    // the tests below that care about the verdict override this.
    http.get("/api/library/readiness", () =>
      HttpResponse.json({
        cells: 9,
        players: 2,
        threshold: 5,
        ordinary_available: 12,
        secrets_available: 4,
        thin: [],
        ready: true,
      }),
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

  it("says what the library is missing instead of showing an empty picker", async () => {
    // The dead end the whole plan exists to close: an empty library leaves
    // one disabled option in the `<select>`, and nothing on screen says why.
    // Kills on: a setup screen without the readiness verdict — the operator
    // hits the mute list and learns neither the reason nor where to go.
    server.use(
      http.get("/api/library/categories", () => HttpResponse.json([])),
      http.get("/api/library/readiness", () =>
        HttpResponse.json({
          cells: 9,
          players: 3,
          threshold: 5,
          ordinary_available: 0,
          secrets_available: 0,
          thin: [],
          ready: false,
        }),
      ),
    );
    renderWithQuery(
      <MatchSetup
        frame={hostFrame({ status: "setup", players: [], player_count: 3 })}
        matchId={MATCH}
        stageToken="t"
      />,
    );
    // §B: `ordinaryNeeded` is `cells - players` (9 - 3 = 6), not `cells`.
    // Kills on: `ordinaryNeeded` collapsing to `readiness.cells` — that
    // mutation still passes an assertion that only checks for
    // `/секретных тем/i`, since this fixture is short on both pools.
    expect(await screen.findByText(/обычных тем 0 из 6/i)).toBeInTheDocument();
    expect(screen.getByText(/секретных тем/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Библиотека" })).toHaveAttribute(
      "href",
      "/host/library",
    );
  });

  it("stays quiet when the library covers the board", async () => {
    stock();
    server.use(
      http.get("/api/library/readiness", () =>
        HttpResponse.json({
          cells: 9,
          players: 3,
          threshold: 5,
          ordinary_available: 12,
          secrets_available: 4,
          thin: [],
          ready: true,
        }),
      ),
    );
    renderWithQuery(
      <MatchSetup frame={hostFrame({ player_count: 2 })} matchId={MATCH} stageToken="t" />,
    );
    // The default frame has two players, each with its own picker, so two
    // "Тайна" options render — one per select.
    await screen.findAllByRole("option", { name: "Тайна" });
    expect(screen.queryByText(/не хватает/i)).not.toBeInTheDocument();
  });

  it("asks readiness about the declared roster, not the one filled in so far", async () => {
    const asked: string[] = [];
    stock();
    server.use(
      http.get("/api/library/readiness", ({ request }) => {
        asked.push(new URL(request.url).search);
        return HttpResponse.json({
          cells: 12,
          players: 4,
          threshold: 5,
          ordinary_available: 12,
          secrets_available: 4,
          thin: [],
          ready: true,
        });
      }),
    );
    renderWithQuery(
      <MatchSetup
        frame={hostFrame({ board: { width: 4, height: 3 }, players: [], player_count: 4 })}
        matchId={MATCH}
        stageToken="t"
      />,
    );
    await waitFor(() => expect(asked).not.toHaveLength(0));
    expect(asked[0]).toContain("cells=12");
    expect(asked[0]).toContain("players=4");
  });

  it("says the readiness check failed rather than going quiet", async () => {
    // Minor 5: `missing` falls back to `[]` while the query is still
    // loading, and a failed request looks identical to that fallback
    // unless it is handled on its own — which would put the operator
    // straight back at the mute empty picker this task exists to close.
    // Kills on: treating `readiness.isError` like the loading state.
    stock();
    server.use(http.get("/api/library/readiness", () => new HttpResponse(null, { status: 500 })));
    renderWithQuery(<MatchSetup frame={hostFrame()} matchId={MATCH} stageToken="t" />);
    expect(await screen.findByText(/не удалось проверить/i)).toBeInTheDocument();
  });
});
