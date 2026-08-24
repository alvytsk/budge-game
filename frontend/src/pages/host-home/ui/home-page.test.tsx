import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { renderWithQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { HomePage } from "./home-page";

const MATCH = {
  id: "33333333-3333-3333-3333-333333333333",
  status: "running",
  winner_id: null,
  last_seq: 12,
  players: [
    { name: "Аня", colour: "#e4572e", eliminated: false },
    { name: "Борис", colour: "#2e86e4", eliminated: true },
  ],
};

describe("HomePage", () => {
  it("lists the matches the server knows about", async () => {
    server.use(http.get("/api/matches", () => HttpResponse.json([MATCH])));
    renderWithQuery(<HomePage />);
    expect(await screen.findByText("Аня")).toBeInTheDocument();
    expect(screen.getByText("Борис")).toBeInTheDocument();
  });

  it("says so plainly when there are none", async () => {
    // Kills on: an empty list rendering as blank space, which reads as a
    // broken screen rather than a fresh install.
    server.use(http.get("/api/matches", () => HttpResponse.json([])));
    renderWithQuery(<HomePage />);
    expect(await screen.findByText("Партий пока нет")).toBeInTheDocument();
  });

  it("marks an eliminated player as out", async () => {
    server.use(http.get("/api/matches", () => HttpResponse.json([MATCH])));
    renderWithQuery(<HomePage />);
    expect(await screen.findByTestId("player-Борис")).toHaveAttribute("data-eliminated", "true");
    expect(screen.getByTestId("player-Аня")).toHaveAttribute("data-eliminated", "false");
  });
  it("creates a match with the board and player count that were chosen", async () => {
    // §2.1 allows any width and height; §2.2 any player count from two
    // up. Kills on: a hardcoded board — the operator could run one shape
    // of game and no other.
    const made: unknown[] = [];
    server.use(
      http.get("/api/matches", () => HttpResponse.json([])),
      http.post("/api/matches", async ({ request }) => {
        made.push(await request.json());
        return HttpResponse.json(
          { outcome: "accepted", match_id: MATCH.id, stage_token: "tok" },
          { status: 201 },
        );
      }),
    );
    renderWithQuery(<HomePage />);
    await userEvent.clear(await screen.findByLabelText("Ширина"));
    await userEvent.type(screen.getByLabelText("Ширина"), "5");
    await userEvent.clear(screen.getByLabelText("Высота"));
    await userEvent.type(screen.getByLabelText("Высота"), "3");
    await userEvent.clear(screen.getByLabelText("Игроков"));
    await userEvent.type(screen.getByLabelText("Игроков"), "4");
    await userEvent.click(screen.getByRole("button", { name: "Новая партия" }));
    await waitFor(() =>
      expect(made).toEqual([{ board: { width: 5, height: 3 }, player_count: 4 }]),
    );
  });

  it("will not offer a board side the rules forbid", () => {
    // §2.1: W >= 3 and H >= 3. Today the operator learns this from the
    // server, by refusal.
    server.use(http.get("/api/matches", () => HttpResponse.json([])));
    renderWithQuery(<HomePage />);
    expect(screen.getByLabelText("Ширина")).toHaveAttribute("min", "3");
    expect(screen.getByLabelText("Высота")).toHaveAttribute("min", "3");
  });

  it("names the rule a board breaks before anything is sent", async () => {
    // 5x4 = 20 cells on three players: 20 does not divide by 3, and that
    // is the only one of §2.1's three conditions broken here.
    server.use(http.get("/api/matches", () => HttpResponse.json([])));
    renderWithQuery(<HomePage />);
    await userEvent.clear(screen.getByLabelText("Ширина"));
    await userEvent.type(screen.getByLabelText("Ширина"), "5");
    await userEvent.clear(screen.getByLabelText("Высота"));
    await userEvent.type(screen.getByLabelText("Высота"), "4");
    expect(await screen.findByText(/не делится на 3/i)).toBeInTheDocument();
  });
});
