import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import type { ReadinessBody } from "@/shared/api";
import { renderWithQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { LibraryPage } from "./library-page";

const CATEGORIES = [
  { id: "a", title: "Кино", is_secret: false, is_active: true, version: 1, active_image_count: 12 },
  { id: "b", title: "Тайна", is_secret: true, is_active: true, version: 1, active_image_count: 3 },
];

const READY = {
  cells: 12,
  players: 0,
  threshold: 5,
  ordinary_available: 40,
  secrets_available: 6,
  thin: [],
  ready: true,
};

function stock(readiness: ReadinessBody = READY) {
  server.use(
    http.get("/api/library/categories", () => HttpResponse.json(CATEGORIES)),
    http.get("/api/library/readiness", () => HttpResponse.json(readiness)),
    http.get("/api/library/categories/:id", () =>
      HttpResponse.json({ category: CATEGORIES[0], images: [] }),
    ),
  );
}

describe("LibraryPage", () => {
  it("lists every category with its picture count", async () => {
    stock();
    renderWithQuery(<LibraryPage />);
    expect(await screen.findByText("Кино")).toBeInTheDocument();
    expect(screen.getByTestId("count-a")).toHaveTextContent("12");
  });

  it("marks which categories are secrets", async () => {
    // §2.3: a secret is a different kind of thing, and the operator picks
    // one per player. Kills on: rendering both alike.
    stock();
    renderWithQuery(<LibraryPage />);
    await screen.findByText("Тайна");
    expect(screen.getByTestId("category-b")).toHaveAttribute("data-secret", "true");
    expect(screen.getByTestId("category-a")).toHaveAttribute("data-secret", "false");
  });

  it("names a thin category even when the server calls the library ready", async () => {
    // §D: the server's own `ready` verdict never looks at `thin`
    // (`ordinary_available >= cells - players && secrets_available >=
    // players`) — both pools can already cover the board while one
    // category is running low. `READY` here is a fixture the server could
    // actually emit (40 ordinary against 12 cells, 6 secrets against 0
    // players — both satisfied, `ready: true`) with `thin` added; this is
    // the exact state that was silently unreachable when the badge gated
    // all naming behind `ready`.
    //
    // `data-ready` is keyed off `missing.length === 0`, not off the
    // server's raw `ready`, so it agrees with the text: a thin category
    // still names a shortfall, and the badge must render amber for it
    // rather than the muted grey a stale `ready: true` would give.
    // §8: «мягкое предупреждение» still holds — nothing here blocks.
    stock({ ...READY, thin: [{ id: "b", title: "Тайна", active_image_count: 3 }] });
    renderWithQuery(<LibraryPage />);
    await screen.findByText("Кино");
    expect(screen.getByTestId("readiness")).toHaveAttribute("data-ready", "false");
    expect(screen.getByTestId("readiness")).toHaveTextContent("Тайна");
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("opens a category for editing when it is picked", async () => {
    stock();
    renderWithQuery(<LibraryPage />);
    await userEvent.click(await screen.findByText("Кино"));
    await waitFor(() => expect(screen.getByLabelText("Название")).toBeInTheDocument());
  });

  it("creates a category from the form", async () => {
    const made: unknown[] = [];
    stock();
    server.use(
      http.post("/api/library/categories", async ({ request }) => {
        made.push(await request.json());
        return HttpResponse.json(CATEGORIES[0], { status: 201 });
      }),
    );
    renderWithQuery(<LibraryPage />);
    await userEvent.type(await screen.findByLabelText("Новая тема"), "Музыка");
    await userEvent.click(screen.getByRole("button", { name: "Создать" }));
    await waitFor(() => expect(made).toEqual([{ title: "Музыка", is_secret: false }]));
  });

  it("names the real reason instead of always blaming thin categories", async () => {
    // Today the message is always the same one, and with an empty `thin`
    // the operator reads literally "Мало картинок: —".
    // Kills on: the one wording — the operator goes looking for pictures
    // where what is actually short is themes.
    server.use(
      http.get("/api/library/categories", () => HttpResponse.json([])),
      http.get("/api/library/readiness", () =>
        HttpResponse.json({
          cells: 12,
          players: 0,
          threshold: 5,
          ordinary_available: 3,
          secrets_available: 0,
          thin: [],
          ready: false,
        }),
      ),
    );
    renderWithQuery(<LibraryPage />);
    const badge = await screen.findByTestId("readiness");
    expect(badge).toHaveTextContent(/обычных тем/i);
    expect(badge).not.toHaveTextContent("—");
  });

  it("names the secret shortfall when the library holds only ordinary themes", async () => {
    // §B/§D: DEFAULT_PLAYERS must actually reach useReadiness, or
    // `secrets_available < readiness.players` can never fire and a
    // library of twelve ordinary themes and zero secret ones renders
    // "Тем достаточно". Kills on: `useReadiness(DEFAULT_CELLS)` with
    // `players` left at its default of 0.
    //
    // The msw handler recomputes `secrets_available < players` itself from
    // the request's own query string, the way the real route does — a
    // fixture with `players` merely baked into the JSON body would pass
    // regardless of what the component actually asked for, since msw does
    // not enforce the query string on its own.
    const asked: string[] = [];
    server.use(
      http.get("/api/library/categories", () => HttpResponse.json([])),
      http.get("/api/library/readiness", ({ request }) => {
        asked.push(new URL(request.url).search);
        const players = Number(new URL(request.url).searchParams.get("players") ?? 0);
        return HttpResponse.json({
          cells: 12,
          players,
          threshold: 5,
          ordinary_available: 40,
          secrets_available: 0,
          thin: [],
          ready: players === 0,
        });
      }),
    );
    renderWithQuery(<LibraryPage />);
    const badge = await screen.findByTestId("readiness");
    expect(badge).toHaveTextContent(/секретных тем/i);
    expect(asked[0]).toContain("players=3");
  });

  it("creates a secret theme in one step", async () => {
    // §E: without the checkbox, a secret theme takes four steps, three of
    // which exist only because the first one did not ask.
    const sent: { title: string; is_secret: boolean }[] = [];
    server.use(
      http.get("/api/library/categories", () => HttpResponse.json([])),
      http.get("/api/library/readiness", () =>
        HttpResponse.json({
          cells: 12,
          players: 0,
          threshold: 5,
          ordinary_available: 0,
          secrets_available: 0,
          thin: [],
          ready: false,
        }),
      ),
      http.post("/api/library/categories", async ({ request }) => {
        sent.push((await request.json()) as { title: string; is_secret: boolean });
        return HttpResponse.json({}, { status: 201 });
      }),
    );
    renderWithQuery(<LibraryPage />);
    await userEvent.type(screen.getByLabelText("Новая тема"), "Тайна Киры");
    await userEvent.click(screen.getByLabelText("Секретная"));
    await userEvent.click(screen.getByRole("button", { name: "Создать" }));
    await waitFor(() => expect(sent).toEqual([{ title: "Тайна Киры", is_secret: true }]));
  });
});
