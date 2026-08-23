import { colourOf } from "@/entities/match";
import type { StageFrame } from "@/shared/api";

export interface EndgameOverlayProps {
  frame: StageFrame;
  winner: string | null;
}

/** §9.1's «выбывание / победа — отдельный кадр». */
export function EndgameOverlay({ frame, winner }: EndgameOverlayProps) {
  const champion = frame.players.find((player) => player.id === winner) ?? null;
  const eliminated = frame.players.filter((player) => player.eliminated);

  return (
    <div className="flex h-full flex-col items-center justify-center gap-10">
      <span className="font-display text-7xl uppercase tracking-widest text-stage-muted">
        {champion ? "Победа" : "Игра окончена"}
      </span>
      {champion && (
        <span
          className="font-display text-[10rem] uppercase leading-none"
          style={{ color: colourOf(frame, champion.id) }}
        >
          {champion.name}
        </span>
      )}
      {eliminated.length > 0 && (
        <ul className="flex gap-8 font-sans text-3xl text-stage-muted">
          {eliminated.map((player) => (
            <li key={player.id} className="line-through">
              {player.name}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
