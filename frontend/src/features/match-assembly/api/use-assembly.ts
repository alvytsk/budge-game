import { type UseMutationResult, useMutation, useQueryClient } from "@tanstack/react-query";
import type { CreatedMatchBody, CreateMatchBody, OutcomeBody } from "@/shared/api";

/** Every assembly route answers the same outcome envelope, and a refusal
 * is an ordinary answer (§6.3): a 409 carries a `reason` the operator
 * reads, not an exception. Only a server fault throws. */
async function outcome(input: string, init?: RequestInit): Promise<OutcomeBody> {
  const response = await fetch(input, init);
  if (response.status >= 500) throw new Error(`${input}: ${response.status}`);
  return (await response.json()) as OutcomeBody;
}

function post(value?: unknown): RequestInit {
  if (value === undefined) return { method: "POST" };
  return {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(value),
  };
}

export function useCreateMatch(): UseMutationResult<CreatedMatchBody, Error, CreateMatchBody> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (input: CreateMatchBody) => {
      const response = await fetch("/api/matches", post(input));
      if (!response.ok) throw new Error(`create: ${response.status}`);
      return (await response.json()) as CreatedMatchBody;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["matches"] });
    },
  });
}

export function useAddPlayer() {
  return useMutation({
    mutationFn: ({
      matchId,
      ...rest
    }: {
      matchId: string;
      player_id: string;
      name: string;
      colour: string;
    }) => outcome(`/api/matches/${matchId}/players`, post(rest)),
  });
}

export function useAssignSecret() {
  return useMutation({
    mutationFn: ({ matchId, ...rest }: { matchId: string; player_id: string; category: string }) =>
      outcome(`/api/matches/${matchId}/secrets`, post(rest)),
  });
}

export function useDeal() {
  return useMutation({
    mutationFn: ({ matchId }: { matchId: string }) =>
      outcome(`/api/matches/${matchId}/deal`, post()),
  });
}

export function useStart() {
  return useMutation({
    mutationFn: ({ matchId }: { matchId: string }) =>
      outcome(`/api/matches/${matchId}/start`, post()),
  });
}

/** §A.8: both buttons are one command with a flag. Their `evolve`
 * behaviour diverges by a single line, and two routes for one boolean
 * would be two things where one suffices. */
export function useReset() {
  return useMutation({
    mutationFn: ({ matchId, keep_roster }: { matchId: string; keep_roster: boolean }) =>
      outcome(`/api/matches/${matchId}/reset`, post({ keep_roster })),
  });
}
