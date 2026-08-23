import type { CellFrame, StageFrame } from "@/shared/api";
import { CELL, GroupShape } from "./group-shape";

export interface BoardProps {
  frame: StageFrame;
  /** Group ids the current beat wants picked out (§9.1 beat 1). */
  highlight?: string[];
  /** Cells a capture just absorbed (§9.1 beat 3, R4). */
  arriving?: CellFrame[];
}

export function Board({ frame, highlight = [], arriving = [] }: BoardProps) {
  const lit = new Set(highlight);
  const arrived = new Set(arriving.map((cell) => `${cell.col},${cell.row}`));

  return (
    <svg
      viewBox={`0 0 ${frame.board.width * CELL} ${frame.board.height * CELL}`}
      className="h-full w-full"
      role="img"
      aria-label="Поле"
    >
      <defs>
        <pattern
          id="budge-hatch"
          width={16}
          height={16}
          patternUnits="userSpaceOnUse"
          patternTransform="rotate(45)"
        >
          <rect width={16} height={16} fill="#0b0d12" fillOpacity={0.35} />
          <line
            x1={0}
            y1={0}
            x2={0}
            y2={16}
            stroke="#f4f6fb"
            strokeOpacity={0.45}
            strokeWidth={4}
          />
        </pattern>
      </defs>
      {frame.groups.map((group) => (
        <GroupShape
          key={group.id}
          frame={frame}
          group={group}
          highlighted={lit.has(group.id)}
          arriving={arrived}
        />
      ))}
    </svg>
  );
}
