import { describe, expect, it } from "vitest";
import { ATTACKER, DEFENDER, timing } from "../../../../testing/frames";
import { remainingAt } from "./timing";

const anchorMs = Date.parse("2026-08-23T20:00:00Z");

describe("remainingAt", () => {
  it("returns the frame's own value for the player who is not answering", () => {
    const t = timing({ remaining_ms: { [ATTACKER]: 40_000, [DEFENDER]: 55_000 } });
    expect(remainingAt(t, DEFENDER, anchorMs + 9_000)).toBe(55_000);
  });

  it("counts the answering player's clock down from the anchor", () => {
    // Kills on: showing `remaining_ms` unchanged for the answerer, which
    // is a frozen timer on a projector between messages — the exact
    // failure §7.3 exists to prevent.
    const t = timing({ remaining_ms: { [ATTACKER]: 40_000, [DEFENDER]: 55_000 } });
    expect(remainingAt(t, ATTACKER, anchorMs + 9_000)).toBe(31_000);
  });

  it("does not run the clock while the duel is paused", () => {
    // Kills on: ignoring `paused` — §4.4's recovery pause would burn the
    // answerer's budget while the room waits.
    const t = timing({ paused: true, remaining_ms: { [ATTACKER]: 40_000, [DEFENDER]: 55_000 } });
    expect(remainingAt(t, ATTACKER, anchorMs + 9_000)).toBe(40_000);
  });

  it("never goes below zero", () => {
    const t = timing({ remaining_ms: { [ATTACKER]: 4_000, [DEFENDER]: 55_000 } });
    expect(remainingAt(t, ATTACKER, anchorMs + 90_000)).toBe(0);
  });

  it("does not run backwards if a frame arrives stamped before its anchor", () => {
    // Clock skew between the frame's `server_now` and its `anchor` would
    // otherwise show a timer counting up.
    const t = timing({ remaining_ms: { [ATTACKER]: 40_000, [DEFENDER]: 55_000 } });
    expect(remainingAt(t, ATTACKER, anchorMs - 5_000)).toBe(40_000);
  });

  it("reports zero for a player the timing frame does not mention", () => {
    const t = timing({ remaining_ms: {} });
    expect(remainingAt(t, ATTACKER, anchorMs)).toBe(0);
  });

  it("holds still when there is no anchor yet", () => {
    // Beat 1: the attack is declared but the duel has not started, so
    // neither clock has begun.
    const t = timing({ anchor: null, remaining_ms: { [ATTACKER]: 40_000, [DEFENDER]: 55_000 } });
    expect(remainingAt(t, ATTACKER, anchorMs + 30_000)).toBe(40_000);
  });
});
