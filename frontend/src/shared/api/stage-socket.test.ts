import { describe, expect, it, vi } from "vitest";
import { fakeSocketFactory } from "../../../testing/fake-socket";
import { stageFrame } from "../../../testing/frames";
import { connectStage } from "./stage-socket";

describe("connectStage", () => {
  it("reports each frame it receives", () => {
    const { factory, sockets } = fakeSocketFactory();
    const onFrame = vi.fn();
    connectStage({ url: "ws://x/ws/stage/t", onFrame, factory });

    sockets[0]?.open();
    sockets[0]?.deliver(stageFrame({ seq: 4 }));
    sockets[0]?.deliver(stageFrame({ seq: 5 }));

    expect(onFrame.mock.calls.map(([f]) => f.seq)).toEqual([4, 5]);
  });

  it("ignores a message that is not a stage frame", () => {
    // The socket is read-only, but the hub may one day publish another
    // `kind`. Kills on: passing everything through, which would put an
    // object with no `groups` into the renderer and blank the projector.
    const { factory, sockets } = fakeSocketFactory();
    const onFrame = vi.fn();
    connectStage({ url: "ws://x", onFrame, factory });

    sockets[0]?.open();
    sockets[0]?.deliver({ kind: "ack", outcome: "accepted" });
    sockets[0]?.deliverRaw("not json at all");

    expect(onFrame).not.toHaveBeenCalled();
  });

  it("reconnects after the server drops the connection", async () => {
    // Kills on: giving up on close. A projector that loses the socket
    // during an ad break must come back on its own — nobody is at the
    // machine.
    vi.useFakeTimers();
    const { factory, sockets } = fakeSocketFactory();
    connectStage({ url: "ws://x", onFrame: vi.fn(), factory });

    sockets[0]?.open();
    sockets[0]?.serverClose();
    await vi.advanceTimersByTimeAsync(1000);

    expect(sockets).toHaveLength(2);
    vi.useRealTimers();
  });

  it("stops reconnecting once disposed", () => {
    // Kills on: leaving the retry timer armed after unmount — React 19
    // StrictMode mounts twice, and a leaked retry loop doubles the
    // subscriber count on the server on every remount.
    vi.useFakeTimers();
    const { factory, sockets } = fakeSocketFactory();
    const dispose = connectStage({ url: "ws://x", onFrame: vi.fn(), factory });

    sockets[0]?.open();
    dispose();
    sockets[0]?.serverClose();
    vi.advanceTimersByTime(60_000);

    expect(sockets).toHaveLength(1);
    expect(sockets[0]?.closed).toBe(true);
    vi.useRealTimers();
  });
});
