import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { StageGroupFrame } from "@/shared/api";
import { ATTACKER, cells, DEFENDER, group, stageFrame } from "../../../../testing/frames";
import { Board } from "./board";

describe("Board", () => {
  it("names a revealed category, in large type", () => {
    render(<Board frame={stageFrame()} />);
    expect(screen.getByText("Кино")).toBeInTheDocument();
  });

  it("labels an unrevealed group «Секрет» and never its name", () => {
    // §9.1 and R8 — the outermost rung of §11's projection ladder: after
    // the Python frame and the TypeScript type, that nothing but the word
    // reaches the DOM.
    const frame = stageFrame({
      groups: [group({ id: "g2", owner: DEFENDER, category: { kind: "hidden" }, revealed: false })],
    });
    render(<Board frame={frame} />);
    expect(screen.getByText("Секрет")).toBeInTheDocument();
    expect(screen.queryByText("Кино")).not.toBeInTheDocument();
  });

  it("says «Секрет» even when a hidden category arrives carrying a name", () => {
    // R8 through R1: nothing validates the frame at the socket, so the
    // only thing between a server bug and a leaked category on the
    // projector is that this widget reads the name off `kind: "named"`
    // and never off whatever object it was handed. Kills on:
    // `category.name ?? "Секрет"`.
    const leaky = { kind: "hidden", name: "Кино" } as unknown as StageGroupFrame["category"];
    const frame = stageFrame({
      groups: [group({ id: "g2", owner: DEFENDER, category: leaky, revealed: false })],
    });
    render(<Board frame={frame} />);
    expect(screen.getByText("Секрет")).toBeInTheDocument();
    expect(screen.queryByText("Кино")).not.toBeInTheDocument();
  });

  it("hatches an unrevealed group", () => {
    const frame = stageFrame({
      groups: [group({ id: "g2", category: { kind: "hidden" }, revealed: false })],
    });
    const { container } = render(<Board frame={frame} />);
    expect(container.querySelector('[data-hatched="true"]')).not.toBeNull();
  });

  it("fills each group with its owner's colour", () => {
    // Kills on: one colour for everything, which is the whole board
    // becoming unreadable at projector distance.
    const { container } = render(<Board frame={stageFrame()} />);
    const shapes = container.querySelectorAll("[data-group]");
    expect(Array.from(shapes).map((s) => s.getAttribute("data-colour"))).toEqual([
      "#e4572e",
      "#2e86e4",
    ]);
  });

  it("draws one outline per group, not one per cell", () => {
    // Kills on: outlining tiles. §9.1's whole reason for the two border
    // weights is that a group of N cells must read as one object.
    const frame = stageFrame({
      groups: [group({ cells: cells([0, 0], [1, 0], [2, 0]) })],
      board: { width: 3, height: 3 },
    });
    const { container } = render(<Board frame={frame} />);
    expect(container.querySelectorAll("[data-outline]")).toHaveLength(1);
  });

  it("marks the two groups a declared attack names", () => {
    // §9.1 beat 1: «две группы подсвечены».
    const { container } = render(<Board frame={stageFrame()} highlight={["g1", "g2"]} />);
    expect(container.querySelectorAll('[data-highlighted="true"]')).toHaveLength(2);
  });

  it("marks the cells a capture just absorbed", () => {
    // §9.1 beat 3, R4: the board is already the new one; these cells are
    // what animates in.
    const { container } = render(<Board frame={stageFrame()} arriving={cells([1, 0])} />);
    expect(container.querySelectorAll('[data-arriving="true"]')).toHaveLength(1);
  });

  it("sizes its viewBox from the board, not from a constant", () => {
    // Kills on: hardcoding a square. §2.1 allows any width and height,
    // and a wrong viewBox crops the field on the projector.
    const frame = stageFrame({ board: { width: 5, height: 3 } });
    const { container } = render(<Board frame={frame} />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("viewBox")).toBe("0 0 500 300");
  });

  it("does not render a colour for a player who left the frame", () => {
    const frame = stageFrame({ players: [], groups: [group({ owner: ATTACKER })] });
    expect(() => render(<Board frame={frame} />)).not.toThrow();
  });
});
