import type { ReadinessBody } from "@/shared/api";

/** §D: there are three reasons, they are independent, and every one that
 * holds gets named.
 *
 * An empty array means "everything is covered" — the caller decides
 * whether to show anything in its place. */
export function shortfallOf(readiness: ReadinessBody): string[] {
  const missing: string[] = [];
  const ordinaryNeeded = readiness.cells - readiness.players;
  if (readiness.ordinary_available < ordinaryNeeded) {
    missing.push(`обычных тем ${readiness.ordinary_available} из ${ordinaryNeeded}`);
  }
  if (readiness.secrets_available < readiness.players) {
    missing.push(`секретных тем ${readiness.secrets_available} из ${readiness.players}`);
  }
  if (readiness.thin.length > 0) {
    // A dash, not a colon: both call sites prefix this list with
    // "Не хватает: " / "не хватает: ", and a second colon inside one item
    // reads as a nested list ("Не хватает: мало картинок: Тайна").
    missing.push(`мало картинок — ${readiness.thin.map((row) => row.title).join(", ")}`);
  }
  return missing;
}
