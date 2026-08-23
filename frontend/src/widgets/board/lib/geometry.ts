import type { CellFrame } from "@/shared/api";

const key = (col: number, row: number) => `${col},${row}`;

/** The outline of a group: exactly those cell edges that do not face
 * another cell of the same group (R5, §9.1). Drawn as one `<path>` so the
 * group reads as a single object. */
export function outlinePath(cells: CellFrame[], size: number): string {
  const inside = new Set(cells.map((cell) => key(cell.col, cell.row)));
  const segments: string[] = [];

  for (const { col, row } of cells) {
    const x = col * size;
    const y = row * size;
    if (!inside.has(key(col, row - 1))) segments.push(`M${x},${y}L${x + size},${y}`);
    if (!inside.has(key(col + 1, row))) segments.push(`M${x + size},${y}L${x + size},${y + size}`);
    if (!inside.has(key(col, row + 1))) segments.push(`M${x + size},${y + size}L${x},${y + size}`);
    if (!inside.has(key(col - 1, row))) segments.push(`M${x},${y + size}L${x},${y}`);
  }

  return segments.join("");
}

/** Where the group's name goes: the centre of the cell nearest the
 * group's centroid, so an L-shaped group never labels itself over its
 * neighbour. */
export function labelAnchor(cells: CellFrame[], size: number): { x: number; y: number } {
  if (cells.length === 0) return { x: 0, y: 0 };
  const cx = cells.reduce((sum, cell) => sum + cell.col, 0) / cells.length;
  const cy = cells.reduce((sum, cell) => sum + cell.row, 0) / cells.length;
  let best = cells[0] as CellFrame;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (const cell of cells) {
    const distance = (cell.col - cx) ** 2 + (cell.row - cy) ** 2;
    if (distance < bestDistance) {
      bestDistance = distance;
      best = cell;
    }
  }
  return { x: best.col * size + size / 2, y: best.row * size + size / 2 };
}
