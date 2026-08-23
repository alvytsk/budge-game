import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ATTACKER, DEFENDER, duel, stageFrame, timing } from "../../../../testing/frames";
import { TimerPair } from "./timer-pair";

const anchor = Date.parse("2026-08-23T20:00:00Z");

describe("TimerPair", () => {
  it("shows both clocks", () => {
    render(<TimerPair frame={stageFrame()} duel={duel()} now={() => anchor} />);
    expect(screen.getAllByRole("timer")).toHaveLength(2);
  });

  it("counts the answering clock down between frames", () => {
    // §7.3: the smoothness of a projector timer must not depend on the
    // message rate. Kills on: printing `remaining_ms` verbatim.
    const d = duel({
      timing: timing({ remaining_ms: { [ATTACKER]: 60_000, [DEFENDER]: 60_000 } }),
    });
    render(<TimerPair frame={stageFrame()} duel={d} now={() => anchor + 13_000} />);
    expect(screen.getByTestId(`timer-${ATTACKER}`)).toHaveTextContent("0:47");
    expect(screen.getByTestId(`timer-${DEFENDER}`)).toHaveTextContent("1:00");
  });

  it("marks the answering timer with that player's own colour", () => {
    // §9.1 beat 2: «активный выделен цветом игрока». Kills on: one accent
    // colour for whoever is answering, which tells the room a timer is
    // running but not whose.
    render(<TimerPair frame={stageFrame()} duel={duel()} now={() => anchor} />);
    expect(screen.getByTestId(`timer-${ATTACKER}`)).toHaveAttribute("data-active", "true");
    expect(screen.getByTestId(`timer-${ATTACKER}`)).toHaveAttribute("data-colour", "#e4572e");
    expect(screen.getByTestId(`timer-${DEFENDER}`)).toHaveAttribute("data-active", "false");
  });

  it("rounds up, so a running clock never shows 0:00 while time remains", () => {
    const d = duel({ timing: timing({ remaining_ms: { [ATTACKER]: 60_000, [DEFENDER]: 1 } }) });
    render(<TimerPair frame={stageFrame()} duel={d} now={() => anchor} />);
    expect(screen.getByTestId(`timer-${DEFENDER}`)).toHaveTextContent("0:01");
  });

  it("shows both players' names", () => {
    render(<TimerPair frame={stageFrame()} duel={duel()} now={() => anchor} />);
    expect(screen.getByText("Аня")).toBeInTheDocument();
    expect(screen.getByText("Борис")).toBeInTheDocument();
  });
});
