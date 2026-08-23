import { outlinePath } from "@/entities/board";
import { colourOf, inkOn } from "@/entities/match";
import { useSelection } from "@/features/host-commands";
import type { CellFrame, HostFrame } from "@/shared/api";

const CELL = 100;
const THICK = 10;

export interface HostBoardProps {
  frame: HostFrame;
  onDeclare: (attackingGroup: string, defendingGroup: string) => void;
}

/** The grid span a group occupies. For an L-shaped group this is the
 * bounding box, which is deliberately an approximation *here and only
 * here*: this board is an input surface, and the SVG overlay above it
 * still draws the group's true shape. */
function span(cells: CellFrame[]): { col: number; row: number; cols: number; rows: number } {
  // A group with no cells is a server bug, not a case to branch on — it
  // takes one grid slot rather than laying out `Infinity`.
  if (cells.length === 0) return { col: 1, row: 1, cols: 1, rows: 1 };
  const cols = cells.map((cell) => cell.col);
  const rows = cells.map((cell) => cell.row);
  const minCol = Math.min(...cols);
  const minRow = Math.min(...rows);
  return {
    col: minCol + 1,
    row: minRow + 1,
    cols: Math.max(...cols) - minCol + 1,
    rows: Math.max(...rows) - minRow + 1,
  };
}

/** §9.2 between duels. Everything clickable here is derived from
 * `legal_attacks` (H2): the adjacency rule is not checked on this screen,
 * it is made unreachable. */
export function HostBoard({ frame, onDeclare }: HostBoardProps) {
  const remembered = useSelection((state) => state.selected);
  const select = useSelection((state) => state.select);
  const clear = useSelection((state) => state.clear);

  // A remembered selection the newest frame no longer contains is stale:
  // the group merged away while it was picked (§9.1's capture). Treating
  // it as live would leave `targets` empty and every group disabled — and
  // the one click that clears a selection is on the group that has just
  // ceased to exist, so the board would be stuck with no way out. H1
  // makes the frame the authority, and this is what that means here.
  const selected =
    remembered !== null && frame.groups.some((group) => group.id === remembered)
      ? remembered
      : null;

  const targets = selected === null ? [] : (frame.legal_attacks[selected] ?? []);
  const canAttack = (groupId: string) => (frame.legal_attacks[groupId] ?? []).length > 0;

  function click(groupId: string): void {
    if (selected === null) {
      if (canAttack(groupId)) select(groupId);
      return;
    }
    if (groupId === selected) {
      clear();
      return;
    }
    if (targets.includes(groupId)) {
      onDeclare(selected, groupId);
      clear();
    }
  }

  return (
    <div className="relative h-full w-full p-4">
      <div
        className="grid h-full w-full gap-1"
        style={{
          gridTemplateColumns: `repeat(${frame.board.width}, 1fr)`,
          gridTemplateRows: `repeat(${frame.board.height}, 1fr)`,
        }}
      >
        {frame.groups.map((group) => {
          const colour = colourOf(frame, group.owner);
          const box = span(group.cells);
          const isSelected = group.id === selected;
          const legal = targets.includes(group.id);
          // Two states, and only two: a live click target, or dimmed and
          // inert. There is no third "clickable but will be refused".
          const live = selected === null ? canAttack(group.id) : isSelected || legal;

          return (
            <button
              key={group.id}
              type="button"
              data-testid={`group-${group.id}`}
              data-selected={isSelected}
              data-legal={selected === null ? undefined : legal}
              disabled={!live}
              onClick={() => click(group.id)}
              style={{
                background: colour,
                color: inkOn(colour),
                gridColumn: `${box.col} / span ${box.cols}`,
                gridRow: `${box.row} / span ${box.rows}`,
              }}
              className="rounded-lg font-display text-xl uppercase transition-opacity disabled:opacity-25 data-[selected=true]:ring-4 data-[selected=true]:ring-white"
            >
              {group.category.name ?? "—"}
            </button>
          );
        })}
      </div>

      {/* The true shapes, drawn over the buttons and deliberately inert. */}
      <svg
        viewBox={`0 0 ${frame.board.width * CELL} ${frame.board.height * CELL}`}
        preserveAspectRatio="none"
        className="pointer-events-none absolute inset-4"
        aria-hidden="true"
      >
        {frame.groups.map((group) => (
          <path
            key={group.id}
            data-outline={group.id}
            d={outlinePath(group.cells, CELL)}
            fill="none"
            stroke="#0b0d12"
            strokeWidth={THICK}
            strokeLinejoin="round"
          />
        ))}
      </svg>
    </div>
  );
}
