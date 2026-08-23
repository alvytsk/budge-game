import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeSocketFactory } from "../../../../testing/fake-socket";
import {
  ATTACKER,
  DEFENDER,
  duel,
  resolution,
  stageFrame,
  timing,
} from "../../../../testing/frames";
import { StagePage } from "./stage-page";

function mount() {
  const { factory, sockets } = fakeSocketFactory();
  render(<StagePage token="tok" socketFactory={factory} />);
  act(() => {
    sockets[0]?.open();
  });
  return {
    send: (frame: unknown) =>
      act(() => {
        sockets[0]?.deliver(frame);
      }),
  };
}

describe("StagePage", () => {
  it("waits before the first frame rather than drawing an empty board", () => {
    mount();
    expect(screen.getByText("Подключение")).toBeInTheDocument();
  });

  it("shows the board, with both groups picked out, when an attack is declared", () => {
    // §9.1 beat 1.
    const { send } = mount();
    send(stageFrame({ duel: duel({ phase: "declared" }) }));
    expect(screen.getByRole("main")).toHaveAttribute("data-beat", "declared");
    expect(screen.getByRole("img", { name: "Поле" })).toBeInTheDocument();
  });

  it("replaces the board with the picture once the duel runs", () => {
    // §9.1 beat 2: «поле уходит, картинка занимает экран».
    const { send } = mount();
    send(stageFrame({ duel: duel({ phase: "running" }) }));
    expect(screen.queryByRole("img", { name: "Поле" })).toBeNull();
    expect(screen.getByRole("presentation")).toBeInTheDocument();
  });

  it("brings the board back for the capture", () => {
    // §9.1 beat 3, «третий такт обязателен».
    const { send } = mount();
    send(stageFrame({ duel: null, resolution: resolution() }));
    expect(screen.getByRole("main")).toHaveAttribute("data-beat", "capture");
    expect(screen.getByRole("img", { name: "Поле" })).toBeInTheDocument();
  });

  it("lays the pause plaque over the duel without removing it", () => {
    // §9.1: pause is «вне такта». Kills on: making pause a beat, which
    // would take the picture off the screen the room is discussing.
    const { send } = mount();
    send(stageFrame({ duel: duel({ phase: "running", timing: timing({ paused: true }) }) }));
    expect(screen.getByText("Пауза")).toBeInTheDocument();
    expect(screen.getByRole("presentation")).toBeInTheDocument();
  });

  it("shows the endgame frame when the match finishes", () => {
    const { send } = mount();
    send(stageFrame({ status: "finished", winner: DEFENDER }));
    expect(screen.getByText("Победа")).toBeInTheDocument();
    expect(screen.getByText("Борис")).toBeInTheDocument();
  });

  it("keeps the last frame on screen when the socket drops", () => {
    // §7.2. Kills on: rendering the connecting placeholder on a drop,
    // which blanks the projector on a network hiccup.
    const { factory, sockets } = fakeSocketFactory();
    render(<StagePage token="tok" socketFactory={factory} />);
    act(() => {
      sockets[0]?.open();
      sockets[0]?.deliver(stageFrame({ duel: duel({ phase: "running" }) }));
      sockets[0]?.serverClose();
    });
    expect(screen.getByRole("presentation")).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("data-connected", "false");
  });

  it("never puts an unrevealed category's name on the screen", () => {
    // §7.1, §11, R8 — the last rung of the projection ladder: whatever
    // the server sent, what reaches the DOM for a hidden group is the
    // word «Секрет» and nothing else.
    const { send } = mount();
    send(
      stageFrame({
        groups: [
          {
            id: "g9",
            owner: ATTACKER,
            category: { kind: "hidden" },
            cells: [{ col: 0, row: 0 }],
            revealed: false,
          },
        ],
      }),
    );
    const body = screen.getByRole("main").textContent ?? "";
    expect(body).toContain("Секрет");
    expect(body).not.toContain("Кино");
  });

  it("renders the newest frame, not a merge of what came before", () => {
    // §7.2: «клиент рисует последнее пришедшее». Kills on: any attempt to
    // reconcile, patch or queue frames.
    const { send } = mount();
    send(stageFrame({ duel: duel({ phase: "running" }) }));
    send(stageFrame({ duel: null, status: "finished", winner: ATTACKER }));
    expect(screen.queryByRole("presentation")).toBeNull();
    expect(screen.getByText("Аня")).toBeInTheDocument();
  });
});
