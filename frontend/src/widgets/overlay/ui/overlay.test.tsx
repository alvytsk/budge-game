import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ATTACKER, player, stageFrame } from "../../../../testing/frames";
import { EndgameOverlay } from "./endgame-overlay";
import { PauseOverlay } from "./pause-overlay";

describe("PauseOverlay", () => {
  it("says so, large", () => {
    // §9.1: «крупная плашка поверх всего, чтобы зал понимал, что таймер
    // стоит».
    render(<PauseOverlay />);
    expect(screen.getByText("Пауза")).toBeInTheDocument();
  });
});

describe("EndgameOverlay", () => {
  it("names the winner", () => {
    render(<EndgameOverlay frame={stageFrame()} winner={ATTACKER} />);
    expect(screen.getByText("Аня")).toBeInTheDocument();
    expect(screen.getByText("Победа")).toBeInTheDocument();
  });

  it("says the match is over even when nobody won", () => {
    // Kills on: rendering nothing without a winner, which leaves the room
    // staring at a dead board.
    render(<EndgameOverlay frame={stageFrame({ winner: null })} winner={null} />);
    expect(screen.getByText("Игра окончена")).toBeInTheDocument();
  });

  it("lists who was eliminated", () => {
    const frame = stageFrame({
      players: [player(), player({ id: "z", name: "Борис", colour: "#2e86e4", eliminated: true })],
    });
    render(<EndgameOverlay frame={frame} winner={ATTACKER} />);
    expect(screen.getByText("Борис")).toBeInTheDocument();
  });
});
