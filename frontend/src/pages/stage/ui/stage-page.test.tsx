import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
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
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

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

  it("ends the capture beat on its own, without a second frame arriving", () => {
    // R4: the capture lasts CAPTURE_MS from the frame's arrival and then
    // gives the board back — no frame is held back or queued to end it,
    // so the only thing that can re-render the page in the meantime is
    // the rAF loop. Kills on: running that loop only while a clock is
    // ticking, which strands the projector on the capture highlight
    // until the operator declares the next attack.
    vi.setSystemTime(new Date("2026-08-23T20:00:00Z"));
    const pending: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
      pending.push(callback),
    );
    vi.stubGlobal("cancelAnimationFrame", () => {});

    const { send } = mount();
    send(stageFrame({ duel: null, resolution: resolution(), server_now: "2026-08-23T20:00:00Z" }));
    expect(screen.getByRole("main")).toHaveAttribute("data-beat", "capture");

    vi.setSystemTime(new Date("2026-08-23T20:00:03Z"));
    act(() => {
      for (const callback of pending.splice(0)) callback(0);
    });

    expect(screen.getByRole("main")).toHaveAttribute("data-beat", "idle");
    expect(screen.getByRole("img", { name: "Поле" })).toBeInTheDocument();
  });

  it("runs no animation loop while the board is just sitting there", () => {
    // §9.1's between-duel board is the common state, and it is on a
    // projector for hours. Kills on: leaving the rAF loop armed whatever
    // the beat, which repaints an unchanging field sixty times a second
    // for as long as the show lasts.
    const pending: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) =>
      pending.push(callback),
    );
    vi.stubGlobal("cancelAnimationFrame", () => {});

    const { send } = mount();
    send(stageFrame({ duel: null, resolution: null }));

    expect(screen.getByRole("main")).toHaveAttribute("data-beat", "idle");
    expect(pending).toHaveLength(0);
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
