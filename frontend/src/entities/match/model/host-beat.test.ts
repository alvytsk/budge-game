import { describe, expect, it } from "vitest";
import { hostDuel, hostFrame } from "../../../../testing/host-frames";
import { hostBeatOf } from "./host-beat";

describe("hostBeatOf", () => {
  it("is setup before the match starts", () => {
    expect(hostBeatOf(hostFrame({ status: "setup" }))).toBe("setup");
  });

  it("is the board between duels", () => {
    expect(hostBeatOf(hostFrame({ status: "running", duel: null }))).toBe("board");
  });

  it("is judging as soon as an attack is declared", () => {
    // The presenter explains the category during «declared», and the
    // answer and the buttons must already be in front of the operator
    // when the duel starts. Kills on: waiting for `phase === "running"`.
    expect(hostBeatOf(hostFrame({ duel: hostDuel({ phase: "declared" }) }))).toBe("judging");
  });

  it("is judging while the duel runs", () => {
    expect(hostBeatOf(hostFrame({ duel: hostDuel({ phase: "running" }) }))).toBe("judging");
  });

  it("is over once the match finishes, whatever else the frame carries", () => {
    // Kills on: letting a stale duel outrank a finished match, which
    // would leave the operator judging a game that has ended.
    expect(hostBeatOf(hostFrame({ status: "finished", duel: hostDuel() }))).toBe("over");
  });

  it("is total over every reachable frame", () => {
    for (const frame of [
      hostFrame({ status: "setup", groups: [] }),
      hostFrame({ status: "running", groups: [] }),
      hostFrame({ status: "finished", winner: null }),
    ]) {
      expect(hostBeatOf(frame)).toBeTruthy();
    }
  });
});
