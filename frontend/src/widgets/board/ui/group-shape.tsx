import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { fitSize, labelAnchor, labelWidth, outlinePath, wrapLabel } from "@/entities/board";
import { colourOf, inkOn } from "@/entities/match";
import type { StageFrame, StageGroupFrame } from "@/shared/api";

export const CELL = 100;

const THIN = 2;
const THICK = 10;

const LINE_HEIGHT = 1.05;

interface FittedLabelProps {
  text: string;
  x: number;
  y: number;
  fill: string;
  base: number;
  width: number;
}

/** A group's name, wrapped to fit and then shrunk until it does.
 *
 * The shrink is measured, not estimated. Oswald's advance runs from 0.25em
 * on «I» to 0.68em on «Ж» — measured in the browser — so any character
 * count is a poor predictor of width, and a label sized from one either
 * overruns its cell or wastes most of it. `getBBox` answers exactly, once
 * per name; wrapping only decides where the words split, which is
 * forgiving enough for arithmetic.
 */
function FittedLabel({ text, x, y, fill, base, width }: FittedLabelProps) {
  const ref = useRef<SVGTextElement>(null);
  const lines = wrapLabel(text);
  // `null` means "not measured yet", which is also the state the component
  // renders at `base` in — so the one measurement is taken at the size the
  // scale below is relative to.
  // Reset on a rename comes from the `key` at the call site, not an
  // effect: a renamed category is a different width, and remounting is
  // exactly what "forget the old measurement" means.
  const [natural, setNatural] = useState<number | null>(null);

  useLayoutEffect(() => {
    if (natural !== null) return;
    const node = ref.current;
    // jsdom implements neither, and a stage that cannot measure should
    // draw the name at its natural size rather than not at all.
    if (!node || typeof node.getBBox !== "function") return;
    setNatural(node.getBBox().width);
  }, [natural]);

  // The first measurement can land before Oswald has arrived, and the
  // fallback face is wider — every label then sizes itself against a font
  // it is not drawn in and comes out smaller than its cell allows. Once
  // was 23.4 where 26 fitted, measured on the real stage. One more
  // measurement when the faces have settled costs nothing: names change
  // between duels, not between frames.
  useEffect(() => {
    const fonts = document.fonts;
    if (!fonts) return;
    let live = true;
    void fonts.ready.then(() => {
      if (live) setNatural(null);
    });
    return () => {
      live = false;
    };
  }, []);

  const size = natural === null ? base : fitSize(natural, width, base);
  const step = size * LINE_HEIGHT;
  const top = y - ((lines.length - 1) * step) / 2;

  return (
    <text
      ref={ref}
      x={x}
      y={top}
      textAnchor="middle"
      dominantBaseline="central"
      fill={fill}
      className="font-display uppercase"
      fontSize={size}
      style={{ paintOrder: "stroke" }}
    >
      {lines.map((line, index) => (
        <tspan key={line} x={x} y={top + index * step}>
          {line}
        </tspan>
      ))}
    </text>
  );
}

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
      <FittedLabel
        key={label}
        text={label}
        x={anchor.x}
        y={anchor.y}
        fill={inkOn(colour)}
        base={hidden ? 22 : 26}
        width={labelWidth(group.cells, anchor.col, anchor.row, CELL)}
      />
    </g>
  );
}
