import { describe, expect, it } from "vitest";
import { DEFENDER, duel, resolution, stageFrame, timing } from "../../../../testing/frames";
import { beatOf, CAPTURE_MS } from "./beat";

describe("beatOf", () => {
  it("is the board when nothing is happening", () => {
    expect(beatOf(stageFrame(), 0).kind).toBe("idle");
  });

  it("is the declaration while a duel is declared but not running", () => {
    // §9.1 beat 1 — the presenter is explaining the category and the
    // pack is loading.
    const beat = beatOf(stageFrame({ duel: duel({ phase: "declared" }) }), 0);
    expect(beat).toEqual({ kind: "declared", groups: ["g1", "g2"] });
  });

  it("is the duel once it is running", () => {
    expect(beatOf(stageFrame({ duel: duel({ phase: "running" }) }), 0).kind).toBe("duel");
  });

  it("is the capture for a bounded window after a resolution arrives", () => {
    // §9.1: «Третий такт обязателен». Kills on: skipping straight back to
    // the board, which is exactly the merge happening between frames that
    // the spec says loses the room.
    const frame = stageFrame({ duel: null, resolution: resolution() });
    expect(beatOf(frame, 0)).toEqual({ kind: "capture", resolution: resolution() });
    expect(beatOf(frame, CAPTURE_MS - 1).kind).toBe("capture");
  });

  it("returns to the board once the capture window has elapsed", () => {
    // R4: the window closes on its own, without a second frame.
    const frame = stageFrame({ duel: null, resolution: resolution() });
    expect(beatOf(frame, CAPTURE_MS + 1).kind).toBe("idle");
  });

  it("lets a new declaration cut the capture short", () => {
    // R4 and §7.2: the newest frame always wins. Kills on: queueing
    // frames to finish an animation, which puts a stale board on the
    // projector.
    const frame = stageFrame({ duel: duel({ phase: "declared" }), resolution: resolution() });
    expect(beatOf(frame, 0).kind).toBe("declared");
  });

  it("is the endgame once the match is finished", () => {
    // §9.1's «выбывание / победа — отдельный кадр». Kills on: letting a
    // stale resolution or duel outrank it, which would leave the room
    // looking at a board after the game is over.
    const frame = stageFrame({ status: "finished", winner: DEFENDER, resolution: resolution() });
    expect(beatOf(frame, 0)).toEqual({ kind: "endgame", winner: DEFENDER });
  });

  it("does not treat a paused duel as a different beat", () => {
    // §9.1: pause is «состояние вне такта» — an overlay over whatever
    // beat is running, not a beat of its own. Kills on: making pause a
    // beat, which would blank the picture the room is looking at.
    const paused = duel({ phase: "running", timing: timing({ paused: true }) });
    expect(beatOf(stageFrame({ duel: paused }), 0).kind).toBe("duel");
  });

  it("is total over every reachable frame", () => {
    // Kills on: a `default:` branch that returns undefined and blanks the
    // screen on a state nobody thought of.
    const frames = [
      stageFrame({ status: "setup", duel: null }),
      stageFrame({ status: "running", groups: [] }),
      stageFrame({ status: "finished", winner: null }),
      stageFrame({ duel: duel({ phase: "running" }), resolution: resolution() }),
    ];
    for (const frame of frames) {
      expect(beatOf(frame, 0).kind).toBeTruthy();
    }
  });
});
