import type { TimingFrame } from "@/shared/api";

/** What `player`'s clock reads at server time `serverNowMs`.
 *
 * §7.3: the server sends no ticks. `remaining_ms` is what each clock read
 * at `anchor`; only the answering player's runs, and only while the duel
 * is not paused. Everything here is arithmetic on the frame — there is no
 * decision, and no state.
 */
export function remainingAt(timing: TimingFrame, player: string, serverNowMs: number): number {
  const budget = timing.remaining_ms[player] ?? 0;
  const running = timing.answering === player && !timing.paused && timing.anchor !== null;
  if (!running) return budget;
  const elapsed = serverNowMs - Date.parse(timing.anchor as string);
  if (!Number.isFinite(elapsed) || elapsed <= 0) return budget;
  return Math.max(0, budget - elapsed);
}
