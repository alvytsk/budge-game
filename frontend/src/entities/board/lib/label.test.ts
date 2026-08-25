import { describe, expect, it } from "vitest";
import {
  ELLIPSIS,
  fitSize,
  labelWidth,
  MAX_LABEL_LINES,
  MIN_LABEL_SIZE,
  truncateToWidth,
  wrapLabel,
} from "./label";

const cell = (col: number, row: number) => ({ col, row });

describe("wrapLabel", () => {
  it("leaves a name that is one word alone", () => {
    expect(wrapLabel("Секрет")).toEqual(["Секрет"]);
  });

  it("breaks a two-word name onto two lines", () => {
    // Kills on: a wrap that never fires, which is the bug this exists for —
    // the stage drew the whole name on one line and it ran over the cell
    // and into its neighbour's label.
    expect(wrapLabel("Демо: тема 11")).toHaveLength(2);
  });

  it("picks the split with the shortest long line", () => {
    // Kills on: a greedy fill that packs line one to the brim and leaves a
    // stub — the long line still overruns, so the wrap bought nothing.
    //
    // Asserted against every split the word boundaries actually allow,
    // rather than against a balance figure: for four words there are three
    // of them, and what matters is that the longest line is as short as it
    // can be, not that the two lines are equal.
    const text = "Русский рок девяностых годов";
    const words = text.split(" ");
    const alternatives = [1, 2, 3].map((at) => [
      words.slice(0, at).join(" "),
      words.slice(at).join(" "),
    ]);
    const shortestLongLine = Math.min(
      ...alternatives.map((pair) => Math.max(...pair.map((line) => line.length))),
    );

    const lines = wrapLabel(text);
    expect(Math.max(...lines.map((line) => line.length))).toBe(shortestLongLine);
  });

  it("never produces more lines than it was allowed", () => {
    const lines = wrapLabel("один два три четыре пять шесть семь", MAX_LABEL_LINES);
    expect(lines.length).toBeLessThanOrEqual(MAX_LABEL_LINES);
  });

  it("keeps every word, in order", () => {
    // Kills on: a pack that drops the last word into nothing, or reorders —
    // the host reads this name aloud.
    const text = "Демо: секрет 1";
    expect(wrapLabel(text).join(" ")).toBe(text);
  });

  it("leaves a single over-long word whole rather than hyphenating it", () => {
    expect(wrapLabel("Достопримечательности")).toEqual(["Достопримечательности"]);
  });

  it("survives an empty or blank name", () => {
    expect(wrapLabel("")).toEqual([]);
    expect(wrapLabel("   ")).toEqual([]);
  });
});

describe("labelWidth", () => {
  it("gives a one-cell group one cell, less the outline it must clear", () => {
    expect(labelWidth([cell(0, 0)], 0, 0, 100)).toBe(88);
  });

  it("spreads across the group's horizontal run through the anchor", () => {
    // Kills on: a width fixed at one cell — a 2-wide group would shrink its
    // name to a third of the space it actually has.
    expect(labelWidth([cell(0, 0), cell(1, 0)], 0, 0, 100)).toBe(188);
  });

  it("counts the run in both directions from the anchor", () => {
    const row = [cell(0, 0), cell(1, 0), cell(2, 0)];
    expect(labelWidth(row, 1, 0, 100)).toBe(288);
  });

  it("does not count cells in another row", () => {
    // Kills on: a bounding-box width — an L-shaped group would think it had
    // the width of its widest row at every anchor.
    const shape = [cell(0, 0), cell(0, 1), cell(1, 1)];
    expect(labelWidth(shape, 0, 0, 100)).toBe(88);
  });

  it("does not cross a gap in the run", () => {
    const split = [cell(0, 0), cell(2, 0)];
    expect(labelWidth(split, 0, 0, 100)).toBe(88);
  });
});

describe("fitSize", () => {
  it("leaves a name that already fits at its full size", () => {
    expect(fitSize(70, 88, 26)).toBe(26);
  });

  it("shrinks exactly enough to fit, and no further", () => {
    // Kills on: a fixed step down, or a scale applied to the wrong side of
    // the ratio — either wastes the cell or leaves the name over the edge.
    expect(fitSize(132, 88, 26)).toBeCloseTo(17.33, 2);
    expect(fitSize(132, 88, 26) * (132 / 26)).toBeCloseTo(88, 5);
  });

  it("stops at the floor rather than shrinking a name out of legibility", () => {
    expect(fitSize(4000, 88, 26)).toBe(MIN_LABEL_SIZE);
  });

  it("treats an unmeasurable width as no reason to shrink", () => {
    // `getBBox` answers 0 for an element that has not been laid out; a
    // scale from that would divide by zero and blank the stage.
    expect(fitSize(0, 88, 26)).toBe(26);
  });
});

describe("truncateToWidth", () => {
  // A stand-in for the browser's `getSubStringLength`: every character is
  // 10 wide. The real one is not uniform, which is the whole reason the
  // search runs over measurements instead of counts — but a uniform fake
  // makes the arithmetic checkable by hand.
  const each10 = (chars: number) => chars * 10;

  it("leaves a name that already fits untouched", () => {
    expect(truncateToWidth(each10, "Кино", 8, 100)).toBe("Кино");
  });

  it("cuts to the longest prefix that still leaves room for the ellipsis", () => {
    // Budget 100, ellipsis 20, so 80 of text — eight characters at ten each.
    expect(truncateToWidth(each10, "Достопримечательности", 20, 100)).toBe("Достопри…");
  });

  it("never returns something wider than the budget", () => {
    // Kills on: an off-by-one in the search that keeps one character too
    // many — the exact failure the ellipsis exists to prevent.
    const cut = truncateToWidth(each10, "Достопримечательности", 20, 100);
    expect(each10(cut.length - 1) + 20).toBeLessThanOrEqual(100);
  });

  it("drops trailing punctuation and space so the ellipsis sits flush", () => {
    // «Демо: …» reads as two thoughts; «Демо…» reads as one cut short.
    expect(truncateToWidth(each10, "Демо: тема", 20, 80)).toBe("Демо…");
  });

  it("degrades to the ellipsis alone when there is no room for any of it", () => {
    expect(truncateToWidth(each10, "Достопримечательности", 20, 15)).toBe(ELLIPSIS);
  });

  it("survives an empty name", () => {
    expect(truncateToWidth(each10, "", 20, 100)).toBe("");
  });
});
