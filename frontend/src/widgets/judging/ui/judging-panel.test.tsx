import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ATTACKER, hostDuel, hostFrame, timing } from "../../../../testing/host-frames";
import { JudgingPanel } from "./judging-panel";

const anchor = Date.parse("2026-08-23T20:00:00Z");

function panel(duel = hostDuel({ phase: "running" })) {
  const send = vi.fn();
  render(<JudgingPanel frame={hostFrame({ duel })} duel={duel} now={() => anchor} send={send} />);
  return send;
}

describe("JudgingPanel", () => {
  it("puts the correct answer in the centre", () => {
    // §9.2: «правильный ответ гигантским кеглем в центре». This is the
    // one thing the operator's eye must find without searching.
    panel();
    expect(screen.getByTestId("answer")).toHaveTextContent("Титаник");
  });

  it("counts the pictures so the operator sees the category running out", () => {
    // §9.2: «Счётчик картинок нужен не игре, а ведущему».
    panel(hostDuel({ phase: "running", index: 1, image_count: 4 }));
    expect(screen.getByTestId("picture-count")).toHaveTextContent("2 / 4");
  });

  it("shows a thumbnail of the picture the room is looking at", () => {
    panel(hostDuel({ phase: "running", index: 1 }));
    expect(screen.getByAltText("Титаник")).toHaveAttribute("src", `/api/media/${"b".repeat(64)}`);
  });

  it("offers the three judgements, with undo visually quieter", () => {
    // §9.2: «три кнопки внизу. Отмена рядом, но визуально тише».
    panel();
    expect(screen.getByRole("button", { name: "Верно" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Пас" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Пауза" })).toBeInTheDocument();
    expect(screen.getByTestId("undo")).toHaveAttribute("data-quiet", "true");
  });

  it("sends the right command for each button", async () => {
    const send = panel();
    await userEvent.click(screen.getByRole("button", { name: "Верно" }));
    await userEvent.click(screen.getByRole("button", { name: "Пас" }));
    await userEvent.click(screen.getByRole("button", { name: "Пауза" }));
    await userEvent.click(screen.getByTestId("undo"));
    expect(send.mock.calls.map(([command]) => command.type)).toEqual([
      "judge_correct",
      "judge_pass",
      "pause_duel",
      "undo_last_judgement",
    ]);
  });

  it("judges on the space bar and passes on the physical P", () => {
    // §9.2: «Мышью такой темп не выдерживается». H4 for the layout.
    const send = panel();
    document.dispatchEvent(new KeyboardEvent("keydown", { code: "Space", bubbles: true }));
    document.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyP", key: "з", bubbles: true }));
    document.dispatchEvent(new KeyboardEvent("keydown", { code: "Escape", bubbles: true }));
    document.dispatchEvent(
      new KeyboardEvent("keydown", { code: "KeyZ", ctrlKey: true, bubbles: true }),
    );
    expect(send.mock.calls.map(([command]) => command.type)).toEqual([
      "judge_correct",
      "judge_pass",
      "pause_duel",
      "undo_last_judgement",
    ]);
  });

  it("resumes rather than pauses when the duel is already paused", () => {
    // Kills on: sending `pause_duel` twice — the operator's escape key
    // would be a dead key for the whole pause.
    const send = panel(hostDuel({ phase: "running", timing: timing({ paused: true }) }));
    document.dispatchEvent(new KeyboardEvent("keydown", { code: "Escape", bubbles: true }));
    expect(send).toHaveBeenCalledWith({ type: "resume_duel" });
  });

  it("starts the duel rather than judging it while the attack is only declared", () => {
    // Beat 1 on the stage screen is the presenter explaining the
    // category; the console's button there is «Начать дуэль».
    const send = panel(hostDuel({ phase: "declared" }));
    expect(screen.getByRole("button", { name: "Начать дуэль" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Верно" })).toBeNull();
    document.dispatchEvent(new KeyboardEvent("keydown", { code: "Space", bubbles: true }));
    expect(send).toHaveBeenCalledWith({ type: "start_duel" });
  });

  it("shows both clocks", () => {
    panel();
    expect(screen.getAllByRole("timer")).toHaveLength(2);
    expect(screen.getByTestId(`clock-${ATTACKER}`)).toHaveTextContent("1:00");
  });
});
