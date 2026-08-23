import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { duel, stageFrame } from "../../../../testing/frames";
import { DuelView } from "./duel-view";

const anchor = Date.parse("2026-08-23T20:00:00Z");
const A = "a".repeat(64);
const B = "b".repeat(64);

describe("DuelView", () => {
  it("shows the image the duel's index points at", () => {
    const d = duel({ image_order: [A, B], index: 1 });
    render(<DuelView frame={stageFrame()} duel={d} now={() => anchor} />);
    expect(screen.getByRole("presentation")).toHaveAttribute("src", `/api/media/${B}`);
  });

  it("does not fall off the end of the pack", () => {
    // Kills on: indexing blind. A duel that outruns its images must not
    // put a broken image on the projector mid-show.
    const d = duel({ image_order: [A], index: 5 });
    expect(() =>
      render(<DuelView frame={stageFrame()} duel={d} now={() => anchor} />),
    ).not.toThrow();
    expect(screen.queryByRole("presentation")).toBeNull();
  });

  it("carries no answer text anywhere in the DOM", () => {
    // §7.1 and R8, at the last rung: `StageDuelFrame` has no answer field,
    // and this asserts nothing reconstructs one.
    const { container } = render(
      <DuelView frame={stageFrame()} duel={duel()} now={() => anchor} />,
    );
    expect(container.textContent).not.toMatch(/ответ/i);
  });

  it("does not draw the board", () => {
    // §9.1 beat 2: «поле уходит, картинка занимает экран».
    const { container } = render(
      <DuelView frame={stageFrame()} duel={duel()} now={() => anchor} />,
    );
    expect(container.querySelector('[aria-label="Поле"]')).toBeNull();
  });
});
