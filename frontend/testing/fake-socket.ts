export interface SocketLike {
  close(): void;
  send(data: string): void;
  addEventListener(type: "open" | "message" | "close" | "error", listener: () => void): void;
  onopen: ((this: unknown, ev: unknown) => void) | null;
  onmessage: ((this: unknown, ev: { data: string }) => void) | null;
  onclose: ((this: unknown, ev: { code: number }) => void) | null;
  onerror: ((this: unknown, ev: unknown) => void) | null;
}

export type SocketFactory = (url: string) => SocketLike;

export class FakeSocket implements SocketLike {
  onopen: SocketLike["onopen"] = null;
  onmessage: SocketLike["onmessage"] = null;
  onclose: SocketLike["onclose"] = null;
  onerror: SocketLike["onerror"] = null;
  closed = false;
  readonly sent: string[] = [];

  constructor(readonly url: string) {}

  send(data: string): void {
    this.sent.push(data);
  }

  addEventListener(): void {}

  open(): void {
    this.onopen?.call(this, {});
  }

  deliver(payload: unknown): void {
    this.onmessage?.call(this, { data: JSON.stringify(payload) });
  }

  deliverRaw(data: string): void {
    this.onmessage?.call(this, { data });
  }

  serverClose(code = 1006): void {
    this.onclose?.call(this, { code });
  }

  close(): void {
    this.closed = true;
  }
}

export function fakeSocketFactory(): { factory: SocketFactory; sockets: FakeSocket[] } {
  const sockets: FakeSocket[] = [];
  return {
    sockets,
    factory: (url: string) => {
      const socket = new FakeSocket(url);
      sockets.push(socket);
      return socket;
    },
  };
}
