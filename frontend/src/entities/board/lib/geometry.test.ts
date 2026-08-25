import { describe, expect, it } from "vitest";
import { cells } from "../../../../testing/frames";
import { labelAnchor, outlinePath } from "./geometry";

describe("outlinePath", () => {
  it("traces the four sides of a single cell", () => {
    const path = outlinePath(cells([0, 0]), 10);
    expect(path).toContain("M0,0L10,0");
    expect(path).toContain("M10,0L10,10");
    expect(path).toContain("M10,10L0,10");
    expect(path).toContain("M0,10L0,0");
  });

  it("omits the edge two cells of the group share", () => {
    // §9.1: «толстая рамка по границе группы и тонкая внутри — так группа
    // из N клеток читается как один объект». Kills on: outlining every
    // cell, which draws N tiles in one colour instead of one shape.
    const path = outlinePath(cells([0, 0], [1, 0]), 10);
    // The shared vertical edge runs from (10,0) to (10,10).
    expect(path).not.toContain("M10,0L10,10");
    expect(path).not.toContain("M10,10L10,0");
    expect(path).toContain("M20,0L20,10");
  });

  it("keeps both edges when two cells touch only at a corner", () => {
    // Diagonal neighbours are not one shape: §2.1's adjacency is
    // orthogonal, so a diagonal pair must still read as two outlines.
    const path = outlinePath(cells([0, 0], [1, 1]), 10);
    expect(path).toContain("M10,0L10,10");
    expect(path).toContain("M10,20L10,10");
  });

  it("is empty for a group with no cells", () => {
    expect(outlinePath([], 10)).toBe("");
  });
});

describe("labelAnchor", () => {
  it("centres on the one cell of a single-cell group", () => {
    expect(labelAnchor(cells([0, 0]), 10)).toEqual({ x: 5, y: 5, col: 0, row: 0 });
  });

  it("reports the cell it landed on, not only the point", () => {
    // `labelWidth` measures the group's run through that cell, so the point
    // alone is not enough — and recovering the cell by dividing the point
    // back out would be this arithmetic done twice, in two places.
    expect(labelAnchor(cells([2, 1]), 10)).toEqual({ x: 25, y: 15, col: 2, row: 1 });
  });

  it("puts the label on a cell of the group, not in its empty middle", () => {
    // An L-shaped group's bounding-box centre is outside the group; a
    // label there sits on the neighbour's colour.
    const anchor = labelAnchor(cells([0, 0], [0, 1], [1, 1]), 10);
    expect([
      { x: 5, y: 5, col: 0, row: 0 },
      { x: 5, y: 15, col: 0, row: 1 },
      { x: 15, y: 15, col: 1, row: 1 },
    ]).toContainEqual(anchor);
  });
});
