import { describe, expect, it, vi } from "vitest";
import { fakeSocketFactory } from "../../../testing/fake-socket";
import { hostFrame } from "../../../testing/host-frames";
import { connectHost } from "./host-socket";

describe("connectHost", () => {
  it("routes frames and acks down separate channels", () => {
    // One socket carries two message kinds (§7.6). Kills on: routing by
    // arrival order rather than by the `kind` discriminator — an ack
    // rendered as a frame blanks the console mid-match.
    const { factory, sockets } = fakeSocketFactory();
    const onFrame = vi.fn();
    const onAck = vi.fn();
    connectHost({ url: "ws://x", onFrame, onAck, factory });

    sockets[0]?.open();
    sockets[0]?.deliver({ kind: "ack", correlation_id: "c1", outcome: "accepted" });
    sockets[0]?.deliver(hostFrame({ seq: 9 }));

    expect(onAck).toHaveBeenCalledTimes(1);
    expect(onFrame).toHaveBeenCalledTimes(1);
    expect(onFrame.mock.calls[0]?.[0].seq).toBe(9);
  });

  it("wraps a command in an envelope carrying a correlation id", () => {
    // §7.6: the correlation id is how an operator's retry is matched to
    // its answer. Kills on: sending the bare command.
    const { factory, sockets } = fakeSocketFactory();
    const channel = connectHost({ url: "ws://x", onFrame: vi.fn(), onAck: vi.fn(), factory });
    sockets[0]?.open();
    const id = channel.send({ type: "judge_correct" });

    const sent = JSON.parse(sockets[0]?.sent[0] ?? "{}");
    expect(sent.command).toEqual({ type: "judge_correct" });
    expect(sent.correlation_id).toBe(id);
    expect(id).toBeTruthy();
  });

  it("does not reconnect after an authentication refusal", () => {
    // 1008 means the cookie is gone. Retrying cannot succeed; it would
    // hammer the server forever while the operator stares at a console
    // that never says why it is empty.
    vi.useFakeTimers();
    const { factory, sockets } = fakeSocketFactory();
    const onStatus = vi.fn();
    connectHost({ url: "ws://x", onFrame: vi.fn(), onAck: vi.fn(), onStatus, factory });
    sockets[0]?.open();
    sockets[0]?.serverClose(1008);
    vi.advanceTimersByTime(60_000);

    expect(sockets).toHaveLength(1);
    expect(onStatus).toHaveBeenLastCalledWith("refused");
    vi.useRealTimers();
  });

  it("reconnects after an ordinary drop", () => {
    vi.useFakeTimers();
    const { factory, sockets } = fakeSocketFactory();
    connectHost({ url: "ws://x", onFrame: vi.fn(), onAck: vi.fn(), factory });
    sockets[0]?.open();
    sockets[0]?.serverClose(1006);
    vi.advanceTimersByTime(500);

    expect(sockets).toHaveLength(2);
    vi.useRealTimers();
  });

  it("stops everything on dispose", () => {
    vi.useFakeTimers();
    const { factory, sockets } = fakeSocketFactory();
    const channel = connectHost({ url: "ws://x", onFrame: vi.fn(), onAck: vi.fn(), factory });
    sockets[0]?.open();
    channel.dispose();
    sockets[0]?.serverClose(1006);
    vi.advanceTimersByTime(60_000);

    expect(sockets).toHaveLength(1);
    expect(sockets[0]?.closed).toBe(true);
    vi.useRealTimers();
  });
});
