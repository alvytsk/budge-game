import { screen } from "@testing-library/react";
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
});
