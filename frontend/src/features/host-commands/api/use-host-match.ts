import { useCallback, useEffect, useRef, useState } from "react";
import {
  type Ack,
  connectHost,
  type HostChannel,
  type HostCommand,
  type HostFrame,
  type HostSocketFactory,
  type HostStatus,
} from "@/shared/api";

export interface HostMatch {
  frame: HostFrame | null;
  status: HostStatus | "connecting";
  connected: boolean;
  refusal: Ack | null;
  send: (command: HostCommand) => void;
}

/** The console's live view of one match.
 *
 * H1: nothing here writes `frame` except a frame arriving off the socket.
 * A command goes out and the answer comes back as the next frame; the ack
 * exists only so a refusal can be put in front of the operator.
 */
export function useHostMatch(matchId: string, factory?: HostSocketFactory): HostMatch {
  const [frame, setFrame] = useState<HostFrame | null>(null);
  const [status, setStatus] = useState<HostStatus | "connecting">("connecting");
  const [refusal, setRefusal] = useState<Ack | null>(null);
  const channel = useRef<HostChannel | null>(null);

  // `factory` is a test seam handed in once; re-subscribing on its
  // identity would reconnect on every render.
  // biome-ignore lint/correctness/useExhaustiveDependencies: see above
  useEffect(() => {
    setFrame(null);
    setRefusal(null);
    setStatus("connecting");
    const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    const connection = connectHost({
      url: `${scheme}//${window.location.host}/ws/host/${matchId}`,
      factory,
      onFrame: setFrame,
      onAck: (ack) => setRefusal(ack.outcome === "accepted" || ack.outcome === "noop" ? null : ack),
      onStatus: setStatus,
    });
    channel.current = connection;
    return () => {
      channel.current = null;
      connection.dispose();
    };
  }, [matchId]);

  const send = useCallback((command: HostCommand) => {
    channel.current?.send(command);
  }, []);

  return { frame, status, connected: status === "connected", refusal, send };
}
