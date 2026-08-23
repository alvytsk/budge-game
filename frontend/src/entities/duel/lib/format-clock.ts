/** A remaining-time reading as the operator and the hall both read it.
 *
 * It lives in `entities/duel` beside `remainingAt` rather than in the
 * widget that first needed it: the stage's `TimerPair` and the console's
 * `JudgingPanel` are two widgets, and a widget may not import a widget.
 */
export function formatClock(ms: number): string {
  const seconds = Math.ceil(Math.max(0, ms) / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
