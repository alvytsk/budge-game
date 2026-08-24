import { useReset } from "@/features/match-assembly";

export interface MatchResetProps {
  matchId: string;
}

const PROMPTS = {
  replay: "Переиграть партию? Доска и история будут стёрты, игроки и их секреты останутся.",
  full: "Сбросить партию полностью? Будут стёрты доска, история, игроки и их секреты.",
} as const;

/** §A.8: available in every beat, including mid-duel — the mechanic is
 * verified from the middle, not only after.
 *
 * Confirmation is mandatory: the match runs in front of the room, and
 * these buttons sit right next to the ones the host clicks ten times per
 * duel. */
export function MatchReset({ matchId }: MatchResetProps) {
  const reset = useReset();

  function run(keep_roster: boolean, prompt: string) {
    if (!window.confirm(prompt)) return;
    void reset.mutateAsync({ matchId, keep_roster });
  }

  return (
    <div className="flex gap-2">
      <button
        type="button"
        onClick={() => run(true, PROMPTS.replay)}
        className="rounded-lg bg-white/10 px-3 py-1 text-sm text-stage-muted hover:bg-white/15"
      >
        Переиграть
      </button>
      <button
        type="button"
        onClick={() => run(false, PROMPTS.full)}
        className="rounded-lg bg-white/10 px-3 py-1 text-sm text-stage-muted hover:bg-white/15"
      >
        Сбросить полностью
      </button>
    </div>
  );
}
