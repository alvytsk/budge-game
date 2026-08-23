import type { Ack, Envelope, HostFrame } from "./contracts";

export type HostCommand = Envelope["command"];
export type HostStatus = "connected" | "dropped" | "refused";

export interface HostSocketLike {
  close(): void;
  send(data: string): void;
  onopen: ((this: unknown, ev: unknown) => void) | null;
  onmessage: ((this: unknown, ev: { data: string }) => void) | null;
  onclose: ((this: unknown, ev: { code: number }) => void) | null;
  onerror: ((this: unknown, ev: unknown) => void) | null;
}

export type HostSocketFactory = (url: string) => HostSocketLike;

export interface HostConnection {
  url: string;
  onFrame: (frame: HostFrame) => void;
  onAck: (ack: Ack) => void;
  onStatus?: ((status: HostStatus) => void) | undefined;
  factory?: HostSocketFactory | undefined;
}

export interface HostChannel {
  send: (command: HostCommand) => string;
  dispose: () => void;
}

const FIRST_RETRY_MS = 500;
const MAX_RETRY_MS = 8000;
/** 1008 is "policy violation", which the server uses for "not
 * authenticated". Retrying it cannot succeed. */
const UNAUTHORISED = 1008;

export function connectHost({
  url,
  onFrame,
  onAck,
  onStatus,
  factory,
}: HostConnection): HostChannel {
  const open = factory ?? ((target: string) => new WebSocket(target) as unknown as HostSocketLike);
  let disposed = false;
  let socket: HostSocketLike | null = null;
  let retry: ReturnType<typeof setTimeout> | null = null;
  let backoff = FIRST_RETRY_MS;

  function connect(): void {
    if (disposed) return;
    const current = open(url);
    socket = current;

    current.onopen = () => {
      if (disposed) return;
      backoff = FIRST_RETRY_MS;
      onStatus?.("connected");
    };

    current.onmessage = (event) => {
      if (disposed) return;
      let payload: unknown;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      if (typeof payload !== "object" || payload === null) return;
      // Routed by the discriminator, never by arrival order: one channel
      // carries both, and a frame may land between a command and its ack.
      const kind = (payload as { kind?: unknown }).kind;
      if (kind === "host") onFrame(payload as HostFrame);
      else if (kind === "ack") onAck(payload as Ack);
    };

    current.onclose = (event) => {
      if (disposed) return;
      if (event.code === UNAUTHORISED) {
        onStatus?.("refused");
        return;
      }
      onStatus?.("dropped");
      retry = setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, MAX_RETRY_MS);
    };

    current.onerror = () => current.close();
  }

  connect();

  return {
    send: (command: HostCommand) => {
      const correlationId = crypto.randomUUID();
      socket?.send(JSON.stringify({ correlation_id: correlationId, command }));
      return correlationId;
    },
    dispose: () => {
      disposed = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    },
  };
}
