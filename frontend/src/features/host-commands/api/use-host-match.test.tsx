import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeSocketFactory } from "../../../../testing/fake-socket";
import { hostFrame } from "../../../../testing/host-frames";
import { useHostMatch } from "./use-host-match";

describe("useHostMatch", () => {
  it("holds the last frame", () => {
    const { factory, sockets } = fakeSocketFactory();
    const { result } = renderHook(() => useHostMatch("m1", factory));
    act(() => {
      sockets[0]?.open();
      sockets[0]?.deliver(hostFrame({ seq: 4 }));
      sockets[0]?.deliver(hostFrame({ seq: 11 }));
    });
    expect(result.current.frame?.seq).toBe(11);
  });

  it("surfaces a rejection so the operator can read it", () => {
    // §6.3: a rejection is an ordinary outcome and it has a reason. Kills
    // on: swallowing the ack — the operator presses a button, nothing
    // happens, and nothing says why.
    const { factory, sockets } = fakeSocketFactory();
    const { result } = renderHook(() => useHostMatch("m1", factory));
    act(() => {
      sockets[0]?.open();
      sockets[0]?.deliver({
        kind: "ack",
        correlation_id: "c1",
        outcome: "rejected",
        reason: "not_adjacent",
      });
    });
    expect(result.current.refusal?.reason).toBe("not_adjacent");
  });

  it("clears a refusal once a command is accepted", () => {
    // Kills on: a sticky banner that outlives the problem, which the
    // operator learns to ignore.
    const { factory, sockets } = fakeSocketFactory();
    const { result } = renderHook(() => useHostMatch("m1", factory));
    act(() => {
      sockets[0]?.open();
      sockets[0]?.deliver({ kind: "ack", correlation_id: "c1", outcome: "rejected", reason: "x" });
    });
    expect(result.current.refusal).not.toBeNull();
    act(() => {
      sockets[0]?.deliver({ kind: "ack", correlation_id: "c2", outcome: "accepted" });
    });
    expect(result.current.refusal).toBeNull();
  });

  it("sends a command down the socket", () => {
    const { factory, sockets } = fakeSocketFactory();
    const { result } = renderHook(() => useHostMatch("m1", factory));
    act(() => {
      sockets[0]?.open();
      result.current.send({ type: "pause_duel" });
    });
    expect(JSON.parse(sockets[0]?.sent[0] ?? "{}").command).toEqual({ type: "pause_duel" });
  });

  it("does not touch the frame when a command is sent", () => {
    // H1, the ruling this whole surface rests on. Kills on: patching
    // local state on send — the console would show a judgement the server
    // may still reject.
    const { factory, sockets } = fakeSocketFactory();
    const { result } = renderHook(() => useHostMatch("m1", factory));
    act(() => {
      sockets[0]?.open();
      sockets[0]?.deliver(hostFrame({ seq: 4, round_no: 2 }));
      result.current.send({ type: "judge_correct" });
    });
    expect(result.current.frame?.seq).toBe(4);
    expect(result.current.frame?.round_no).toBe(2);
  });
});
