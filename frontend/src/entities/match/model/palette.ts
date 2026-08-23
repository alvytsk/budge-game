import type { StageFrame } from "@/shared/api";

const UNOWNED = "#39414f";

/** A group is drawn in its owner's colour (§9.1). The colour lives on the
 * player, so an unknown owner is a server bug, not a case to branch on —
 * it renders grey rather than throwing on the projector. */
export function colourOf(frame: StageFrame, playerId: string): string {
  return frame.players.find((player) => player.id === playerId)?.colour ?? UNOWNED;
}

/** Legible ink for text laid on `background`, by the WCAG relative
 * luminance of the fill. */
export function inkOn(background: string): string {
  const hex = background.replace("#", "");
  if (hex.length !== 6) return "#ffffff";
  const channels = [0, 2, 4].map((at) => Number.parseInt(hex.slice(at, at + 2), 16) / 255);
  const linear = channels.map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  const [r = 0, g = 0, b = 0] = linear;
  const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return luminance > 0.45 ? "#101319" : "#ffffff";
}
