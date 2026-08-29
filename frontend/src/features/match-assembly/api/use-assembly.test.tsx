import { act, renderHook } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import type { OutcomeBody } from "@/shared/api";
import { withQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { useCreateMatch, useDeal } from "./use-assembly";

const MATCH = "33333333-3333-3333-3333-333333333333";

describe("useCreateMatch", () => {
  it("hands back the match id and the stage token together", async () => {
    // Ruling 7 on the server: the token is minted once and never stored,
    // so this response is its entire lifecycle. Kills on: dropping it —
    // the operator would have no way to open the projector screen.
    server.use(
      http.post("/api/matches", () =>
        HttpResponse.json(
          { outcome: "accepted", match_id: MATCH, stage_token: "tok-123" },
          { status: 201 },
        ),
      ),
    );
    const { result } = renderHook(() => useCreateMatch(), { wrapper: withQuery() });
    let created = { match_id: "", stage_token: "" };
    await act(async () => {
      created = await result.current.mutateAsync({
        board: { width: 4, height: 3 },
        player_count: 3,
      });
    });
    expect(created.match_id).toBe(MATCH);
    expect(created.stage_token).toBe("tok-123");
  });
});

describe("useDeal", () => {
  it("posts again on a redeal rather than short-circuiting", async () => {
    // §3.4: «Повторный DealBoard — это и есть кнопка "перераздать"».
    // Kills on: guarding the second call behind an idempotency check.
    let calls = 0;
    server.use(
      http.post(`/api/matches/${MATCH}/deal`, () => {
        calls += 1;
        return HttpResponse.json({ outcome: "accepted" });
      }),
    );
    const { result } = renderHook(() => useDeal(), { wrapper: withQuery() });
    await act(async () => {
      await result.current.mutateAsync({ matchId: MATCH });
      await result.current.mutateAsync({ matchId: MATCH });
    });
    expect(calls).toBe(2);
  });

  it("surfaces a refusal instead of throwing", async () => {
    // §6.3 and §8: a content shortfall answers 409 `content_unavailable`
    // and is «обычный отказ, не авария». Kills on: treating it as an
    // error — the operator needs to read the reason and press
    // «перераздать», not meet a crash screen.
    server.use(
      http.post(`/api/matches/${MATCH}/deal`, () =>
        HttpResponse.json({ outcome: "rejected", reason: "content_unavailable" }, { status: 409 }),
      ),
    );
    const { result } = renderHook(() => useDeal(), { wrapper: withQuery() });
    let answer: OutcomeBody = { outcome: "noop" };
    await act(async () => {
      answer = await result.current.mutateAsync({ matchId: MATCH });
    });
    expect(answer.outcome).toBe("rejected");
    expect(answer.reason).toBe("content_unavailable");
  });
});
