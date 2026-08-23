import type { StageFrame } from "./contracts";

export type SocketFactory = (url: string) => WebSocketLike;

export interface WebSocketLike {
  close(): void;
  onopen: ((this: unknown, ev: unknown) => void) | null;
  onmessage: ((this: unknown, ev: { data: string }) => void) | null;
  onclose: ((this: unknown, ev: { code: number }) => void) | null;
  onerror: ((this: unknown, ev: unknown) => void) | null;
}

export interface StageConnection {
  url: string;
  onFrame: (frame: StageFrame) => void;
  onStatus?: (connected: boolean) => void;
  factory?: SocketFactory | undefined;
}

const FIRST_RETRY_MS = 1000;
const MAX_RETRY_MS = 8000;

/** R1: the shape is guaranteed by the generated contract, so this checks
 * only the discriminator — enough to reject anything that is not a stage
 * frame without becoming a second schema. */
function isStageFrame(value: unknown): value is StageFrame {
  return (
    typeof value === "object" && value !== null && (value as { kind?: unknown }).kind === "stage"
  );
}

/** Opens the socket and keeps it open. Returns a disposer; after it runs,
 * nothing reconnects and nothing calls back. */
export function connectStage({ url, onFrame, onStatus, factory }: StageConnection): () => void {
  const open = factory ?? ((target: string) => new WebSocket(target) as unknown as WebSocketLike);
  let disposed = false;
  let socket: WebSocketLike | null = null;
  let retry: ReturnType<typeof setTimeout> | null = null;
  let backoff = FIRST_RETRY_MS;

  function connect(): void {
    if (disposed) return;
    const current = open(url);
    socket = current;

    current.onopen = () => {
      if (disposed) return;
      backoff = FIRST_RETRY_MS;
      onStatus?.(true);
    };

    current.onmessage = (event) => {
      if (disposed) return;
      let payload: unknown;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      if (isStageFrame(payload)) onFrame(payload);
    };

    const reopen = () => {
      if (disposed) return;
      onStatus?.(false);
      retry = setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, MAX_RETRY_MS);
    };

    current.onclose = reopen;
    // An error that does not also close would otherwise strand the socket.
    current.onerror = () => current.close();
  }

  connect();

  return () => {
    disposed = true;
    if (retry) clearTimeout(retry);
    socket?.close();
  };
}
