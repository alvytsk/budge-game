import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeSocketFactory } from "../../../testing/fake-socket";
import { stageFrame } from "../../../testing/frames";
import { useStageFrame } from "./use-stage-frame";

describe("useStageFrame", () => {
  it("holds the last frame it received", () => {
    // §7.2: the whole state arrives in every message and the client draws
    // the last one. Kills on: merging frames, or keeping a history.
    const { factory, sockets } = fakeSocketFactory();
    const { result } = renderHook(() => useStageFrame("tok", factory));

    act(() => {
      sockets[0]?.open();
      sockets[0]?.deliver(stageFrame({ seq: 4, round_no: 1 }));
      sockets[0]?.deliver(stageFrame({ seq: 9, round_no: 3 }));
    });

    expect(result.current.frame?.seq).toBe(9);
    expect(result.current.frame?.round_no).toBe(3);
  });

  it("keeps drawing the last frame after the socket drops", () => {
    // §7.2: «Разрыв в `seq` стоит повествования, а не корректности».
    // Kills on: clearing the frame on close, which blanks the projector
    // the moment the network hiccups.
    const { factory, sockets } = fakeSocketFactory();
    const { result } = renderHook(() => useStageFrame("tok", factory));

    act(() => {
      sockets[0]?.open();
      sockets[0]?.deliver(stageFrame({ seq: 4 }));
      sockets[0]?.serverClose();
    });

    expect(result.current.frame?.seq).toBe(4);
    expect(result.current.connected).toBe(false);
  });

  it("closes the socket on unmount", () => {
    const { factory, sockets } = fakeSocketFactory();
    const { unmount } = renderHook(() => useStageFrame("tok", factory));
    unmount();
    expect(sockets.every((s) => s.closed)).toBe(true);
  });
});
