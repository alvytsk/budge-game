import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";

export const AUTH_PROBE = ["auth-probe"] as const;

export interface Login {
  logIn: (password: string) => Promise<boolean>;
  pending: boolean;
  failed: boolean;
}

/** Log in. Resolves to whether the password was accepted rather than
 * rejecting: a wrong password is an ordinary outcome on this screen, not
 * an error condition. */
export function useLogin(): Login {
  const client = useQueryClient();
  const mutation = useMutation({
    mutationFn: async (password: string) => {
      const response = await fetch("/api/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ password }),
      });
      return response.ok;
    },
    onSuccess: (accepted) => {
      if (accepted) void client.invalidateQueries({ queryKey: AUTH_PROBE });
    },
  });

  const { mutateAsync } = mutation;
  const logIn = useCallback((password: string) => mutateAsync(password), [mutateAsync]);

  return { logIn, pending: mutation.isPending, failed: mutation.data === false };
}

/** Whether this browser holds a live session.
 *
 * The cookie is `HttpOnly` (§7.5), so the only way to know is to ask a
 * protected route. `GET /api/matches` is the cheapest one, and its 401 is
 * the answer.
 */
export function useAuthGate(): { state: "checking" | "in" | "out" } {
  const probe = useQuery({
    queryKey: AUTH_PROBE,
    queryFn: async () => {
      const response = await fetch("/api/matches");
      if (response.status === 401) return false;
      if (!response.ok) throw new Error(`probe: ${response.status}`);
      return true;
    },
    retry: false,
    staleTime: 0,
  });

  if (probe.isPending) return { state: "checking" };
  return { state: probe.data === true ? "in" : "out" };
}

export function useLogout(): () => Promise<void> {
  const client = useQueryClient();
  return useCallback(async () => {
    await fetch("/api/session", { method: "DELETE" });
    // Everything behind the session is now unreadable; dropping every key
    // is simpler and safer than naming each one. The probe is the single
    // exception — `clear()` would evict it too, and an evicted query has
    // no observer left to invalidate, so the gate would keep reporting
    // the session it just destroyed.
    client.removeQueries({ predicate: (query) => query.queryKey[0] !== AUTH_PROBE[0] });
    await client.invalidateQueries({ queryKey: AUTH_PROBE });
  }, [client]);
}
