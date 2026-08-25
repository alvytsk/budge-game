import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ELLIPSIS,
  fitSize,
  labelAnchor,
  labelWidth,
  MIN_LABEL_SIZE,
  outlinePath,
  truncateToWidth,
  wrapLabel,
} from "@/entities/board";
import { colourOf, inkOn } from "@/entities/match";
import type { StageFrame, StageGroupFrame } from "@/shared/api";

export const CELL = 100;

const THIN = 2;
const THICK = 10;
const LINE_HEIGHT = 1.05;

interface Fitted {
  size: number;
  lines: string[];
}

interface FittedLabelProps {
  text: string;
  x: number;
  y: number;
  fill: string;
  base: number;
  width: number;
}

/** A group's name: wrapped, then shrunk, then cut — in that order, and
 * every step decided by measurement rather than by counting characters.
 *
 * Oswald's advance runs from 0.25em on «I» to 0.68em on «Ж», measured in
 * the browser, so no character count predicts width well enough to size or
 * cut from. `getComputedTextLength` and `getSubStringLength` answer
 * exactly, and they answer about the face actually in use.
 *
 * Two passes, and only for names that need the second: the first measures
 * the wrapped lines and settles the size; if that hits the floor the name
 * is still too wide, and the second cuts each line to what fits. Names
 * change between duels, not between frames, so the cost is nothing.
 */
function FittedLabel({ text, x, y, fill, base, width }: FittedLabelProps) {
  const lines = useMemo(() => wrapLabel(text), [text]);
  const lineRefs = useRef<(SVGTSpanElement | null)[]>([]);
  const ellipsisRef = useRef<SVGTSpanElement>(null);
  // `null` means "not measured yet", which is also the state this renders
  // at `base` in — so the measurement is taken at the size it is relative to.
  const [fitted, setFitted] = useState<Fitted | null>(null);

  useLayoutEffect(() => {
    if (fitted !== null) return;

    const spans = lines.map((_, index) => lineRefs.current[index]);
    // jsdom implements none of this. A stage that cannot measure draws the
    // name at its natural size rather than not at all.
    if (spans.some((span) => !span || typeof span.getComputedTextLength !== "function")) return;

    const measured = spans.map((span) => (span as SVGTSpanElement).getComputedTextLength());
    const size = fitSize(Math.max(...measured), width, base);
    if (size > MIN_LABEL_SIZE) {
      setFitted({ size, lines });
      return;
    }

    // At the floor and still over the edge. Everything below is measured at
    // `base` while the final render is at the floor, so the budget is scaled
    // into base units rather than the widths being scaled out of them.
    const budget = width * (base / MIN_LABEL_SIZE);
    const ellipsis = ellipsisRef.current?.getComputedTextLength() ?? 0;
    setFitted({
      size: MIN_LABEL_SIZE,
      lines: lines.map((line, index) => {
        const span = spans[index] as SVGTSpanElement;
        return truncateToWidth(
          (chars) => span.getSubStringLength(0, chars),
          line,
          ellipsis,
          budget,
        );
      }),
    });
  }, [fitted, lines, width, base]);

  // The first measurement can land before Oswald has arrived, and the
  // fallback face is wider — every label then sizes itself against a font
  // it is not drawn in. Once was 23.4 where 26 fitted, measured on the real
  // stage. One more measurement when the faces have settled costs nothing.
  useEffect(() => {
    const fonts = document.fonts;
    if (!fonts) return;
    let live = true;
    void fonts.ready.then(() => {
      if (live) setFitted(null);
    });
    return () => {
      live = false;
    };
  }, []);

  const shown = fitted?.lines ?? lines;
  const size = fitted?.size ?? base;
  const step = size * LINE_HEIGHT;
  const top = y - ((shown.length - 1) * step) / 2;

  return (
    <text
      x={x}
      y={top}
      textAnchor="middle"
      dominantBaseline="central"
      fill={fill}
      className="font-display uppercase"
      fontSize={size}
      style={{ paintOrder: "stroke" }}
    >
      {shown.map((line, index) => (
        <tspan
          key={line}
          ref={(node) => {
            lineRefs.current[index] = node;
          }}
          x={x}
          y={top + index * step}
        >
          {line}
        </tspan>
      ))}
      {/* Measured, never seen: the cut needs the ellipsis's own width, and
          the only exact source for it is the face in use. Rendered only
          while measuring, and hidden while it is. */}
      {fitted === null && (
        <tspan ref={ellipsisRef} visibility="hidden">
          {ELLIPSIS}
        </tspan>
      )}
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
      {/* Keyed on the name: a rename is a different width, and remounting
          is exactly what "forget the old measurement" means. */}
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
