import { colourOf, inkOn } from "@/entities/match";
import type { StageFrame, StageGroupFrame } from "@/shared/api";
import { labelAnchor, outlinePath } from "../lib/geometry";

export const CELL = 100;

const THIN = 2;
const THICK = 10;

export interface GroupShapeProps {
  frame: StageFrame;
  group: StageGroupFrame;
  highlighted: boolean;
  arriving: Set<string>;
}

/** One group: the fill, the thin inner grid, the thick outline, and the
 * label (§9.1). The two border weights are separate elements rather than
 * one stroke width, which is what lets a group of N cells read as one
 * object. */
export function GroupShape({ frame, group, highlighted, arriving }: GroupShapeProps) {
  const colour = colourOf(frame, group.owner);
  const hidden = group.category.kind === "hidden";
  const label = group.category.kind === "named" ? group.category.name : "Секрет";
  const anchor = labelAnchor(group.cells, CELL);

  return (
    <g data-group={group.id} data-colour={colour} data-highlighted={highlighted}>
      {group.cells.map((cell) => (
        <rect
          key={`${cell.col},${cell.row}`}
          x={cell.col * CELL}
          y={cell.row * CELL}
          width={CELL}
          height={CELL}
          fill={colour}
          stroke="#0b0d12"
          strokeWidth={THIN}
          strokeOpacity={0.35}
          data-arriving={arriving.has(`${cell.col},${cell.row}`)}
          className={
            arriving.has(`${cell.col},${cell.row}`) ? "animate-[capture_600ms_ease-out]" : undefined
          }
        />
      ))}
      {hidden && (
        <g data-hatched="true">
          {group.cells.map((cell) => (
            <rect
              key={`h${cell.col},${cell.row}`}
              x={cell.col * CELL}
              y={cell.row * CELL}
              width={CELL}
              height={CELL}
              fill="url(#budge-hatch)"
            />
          ))}
        </g>
      )}
      <path
        data-outline={group.id}
        d={outlinePath(group.cells, CELL)}
        fill="none"
        stroke={highlighted ? "#ffffff" : "#0b0d12"}
        strokeWidth={THICK}
        strokeLinejoin="round"
      />
      <text
        x={anchor.x}
        y={anchor.y}
        textAnchor="middle"
        dominantBaseline="central"
        fill={inkOn(colour)}
        className="font-display uppercase"
        fontSize={hidden ? 22 : 26}
        style={{ paintOrder: "stroke" }}
      >
        {label}
      </text>
    </g>
  );
}
