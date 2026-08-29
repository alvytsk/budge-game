import type { CellFrame } from "@/shared/api";

/** How many lines a group's name may be broken across.
 *
 * Two, not three: the label is centred in one cell, and a third line at a
 * size that still fits the cell's height is smaller than the room can read
 * off a projector. Anything that will not fit in two lines is shrunk
 * instead — see `GroupShape`, which measures rather than estimates. */
export const MAX_LABEL_LINES = 2;

const key = (col: number, row: number) => `${col},${row}`;

/** The width a label may use, in the same units `outlinePath` draws in.
 *
 * The anchor is one cell's centre, so one cell is the floor — but a group
 * that runs horizontally through that cell may spread across its whole
 * run, which is what keeps «Демо: тема 11» on one line in a 2-wide group
 * and breaks it in a 1-wide one.
 *
 * The outline is stroked `THICK` wide and centred on the boundary, so half
 * of it eats into the cell from each side; `INSET` keeps the text off it.
 */
export function labelWidth(cells: CellFrame[], anchorCol: number, anchorRow: number, size: number) {
  const inside = new Set(cells.map((cell) => key(cell.col, cell.row)));
  let left = anchorCol;
  let right = anchorCol;
  while (inside.has(key(left - 1, anchorRow))) left -= 1;
  while (inside.has(key(right + 1, anchorRow))) right += 1;
  const INSET = 12;
  return (right - left + 1) * size - INSET;
}

/** The smallest a name is allowed to get, in the units `outlinePath` draws
 * in. At 8 a 4x3 board fills a 1080-tall projector at roughly 29 px, which
 * still reads from the back of a meeting room.
 *
 * It is a floor and not a guarantee: a single word longer than about
 * thirty characters cannot be broken and cannot be shrunk past this, so it
 * will overrun its cell. That is a content problem — §8 already treats the
 * library's contents as the operator's business — and it is the only case
 * left where a label crosses into its neighbour. */
export const MIN_LABEL_SIZE = 8;

/** The size a label must be drawn at to fit `width`, given how wide it
 * came out at `base`.
 *
 * Separated from the component because it is the one piece of the fitting
 * that is arithmetic rather than measurement, and the one worth pinning
 * with tests: the measurement itself is the browser's answer, but what is
 * done with it is ours.
 */
export function fitSize(natural: number, width: number, base: number): number {
  if (natural <= 0 || natural <= width) return base;
  return Math.max(MIN_LABEL_SIZE, (base * width) / natural);
}

/** Break `text` into at most `maxLines` lines of roughly equal length.
 *
 * Where the break goes, not whether the result fits: character count is a
 * fair proxy for balance but a poor one for width — Oswald's advance runs
 * from 0.25em on «I» to 0.68em on «Ж», measured — so fitting is settled by
 * `getComputedTextLength` at render, and this only decides where the words
 * split.
 *
 * A single word longer than a line is left whole: hyphenating a category
 * name the host reads aloud would change what they say.
 */
export function wrapLabel(text: string, maxLines: number = MAX_LABEL_LINES): string[] {
  const words = text.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];
  if (words.length === 1 || maxLines <= 1) return [words.join(" ")];

  const lines = Math.min(maxLines, words.length);
  const target = Math.ceil(words.join(" ").length / lines);

  const out: string[] = [];
  let current = "";
  for (const word of words) {
    const candidate = current === "" ? word : `${current} ${word}`;
    if (current !== "" && candidate.length > target && out.length < lines - 1) {
      out.push(current);
      current = word;
    } else {
      current = candidate;
    }
  }
  out.push(current);
  return out;
}

/** The character a truncated name ends with. One glyph, not three dots:
 * three periods in Oswald at the floor size read as dirt on the lens. */
export const ELLIPSIS = "…";

/** The longest prefix of `text` that fits `budget` once the ellipsis is
 * added, or `text` itself when the whole of it already fits.
 *
 * `widthOfPrefix` is the browser's own answer for the first n characters —
 * `SVGTextContentElement.getSubStringLength` — so this searches over real
 * widths and never assumes a per-character one. Callers that cannot
 * measure should not call it; there is nothing sensible to guess.
 *
 * Trailing spaces and punctuation are trimmed off the prefix so the result
 * reads «Достопримечат…» rather than «Достопримечат …».
 */
export function truncateToWidth(
  widthOfPrefix: (chars: number) => number,
  text: string,
  ellipsisWidth: number,
  budget: number,
): string {
  if (text.length === 0) return text;
  if (widthOfPrefix(text.length) <= budget) return text;

  const room = budget - ellipsisWidth;
  if (room <= 0) return ELLIPSIS;

  // Binary search the largest prefix that still leaves room for the
  // ellipsis. `low` is always a fitting length, `high` always a failing one.
  let low = 0;
  let high = text.length;
  while (high - low > 1) {
    const middle = Math.floor((low + high) / 2);
    if (widthOfPrefix(middle) <= room) low = middle;
    else high = middle;
  }
  return `${text
    .slice(0, low)
    .trimEnd()
    .replace(/[.,:;-]+$/, "")}${ELLIPSIS}`;
}
