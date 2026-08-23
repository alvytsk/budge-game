export function stageSocketUrl(token: string): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/ws/stage/${encodeURIComponent(token)}`;
}

export function mediaUrl(digest: string): string {
  return `/api/media/${digest}`;
}
