# Budge Host Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `/host` surface of §9.2 — the operator's console: setup (players, colours, secrets, library, deal), the between-duels board where an illegal attack is impossible rather than rejected, and the judging screen laid out for one decision every five seconds.

**Architecture:** The second route tree of the application the stage plan scaffolded, in the same Vite + React + FSD project, sharing `shared/api/contracts.ts`. Two transports, exactly as §7.4 divides them: **REST** for everything that assembles a match or edits the library, through TanStack Query; **WebSocket** (`/ws/host/:matchId`) for the live match, carrying commands out and whole frames back. The console never optimistically updates — it submits, and the next frame is the truth. The board's geometry moves out of the stage widget into `entities/board` so both boards share it without one widget importing another.

**Tech Stack:** Everything the stage plan installed, plus `@tanstack/react-query` (the REST surface), `zustand` (one piece of cross-screen client state) and `msw` (test doubles for the REST surface). No new toolchain.

**Spec:** `docs/superpowers/specs/2026-08-22-podvinsya-design.md` — §9.2 primarily; §7.4–7.6 for the transports and the principal; §8 for content administration; §5.3 for the library; §3.4 for redeal; §11 for what the tests must hold.

**Predecessor:** `docs/superpowers/plans/2026-08-23-budge-stage-screen.md`, complete on branch `feature/stage`. Read its **Rulings**: R1 (types only, no Zod), R2 (clock offset from `server_now`) and R6 (dependencies added when needed) all carry forward unchanged.

## Global Constraints

- **Naming.** The product is **budge**. Do not introduce the word "Podvinsya" anywhere under `frontend/`. The Python package is still `podvinsya`; its rename is a separate, later plan and is out of scope here. `contracts.ts`'s generation header is the one permitted mention.
- **User-facing copy is Russian.** «Раздать», «Перераздать», «Верно», «Пас», «Пауза», «Отмена».
- **Package manager is pnpm.** Never `npm` or `yarn`.
- **`frontend/src/shared/api/contracts.ts` is generated and committed.** Never hand-edit it.
- **Import discipline** (Steiger enforces all of these, and no rule may be disabled to get around them):
  - Outside the `shared` layer, import from a layer barrel — `@/shared/api`, `@/shared/config`, `@/entities/match`, `@/widgets/...`. Never a deep path like `@/shared/api/contracts`; `fsd/no-public-api-sidestep` errors on it. The one exception already in the tree is `@/shared/lib/*`, which the stage plan imports directly and Steiger permits.
  - Inside the `shared` layer, use relative imports through the neighbouring barrel (`../config`), never the `@/` alias; `fsd/import-locality` is `error`.
  - A widget may not import another widget, and a widget may not import a page. Shared board maths therefore lives in `entities/board` (H3).
- **Biome**: two-space indent, `lineWidth: 100`, sorted import specifiers, `noExplicitAny: error`, and `a11y/noRedundantRoles` — never write a `role` a semantic element already implies. `pnpm fix` handles the formatting.
- **`pnpm build`, `pnpm check` and `pnpm test` must all be green at every commit.** `pnpm build` is `tsc --noEmit && vite build`, so `tsc` runs *before* the router plugin regenerates `routeTree.gen.ts`: **after adding any route file, run `pnpm exec vite build` once first**, then `pnpm build`.
- **All work happens on branch `feature/host`**, created off `feature/stage` (this plan builds on that tree and cannot compile without it). Never commit to `main`.
- **Every command the console sends goes over the WebSocket**, never REST. Every library or assembly call goes over REST, never the socket. §7.4 draws this line and nothing here crosses it.

---

## The server surface this consumes

Established by earlier plans; treat as fixed. All paths are same-origin and relative.

| Method | Path | Body → Response |
| --- | --- | --- |
| POST | `/api/session` | `LoginBody` → 204 + `HttpOnly` cookie, or **401** |
| DELETE | `/api/session` | → 204 (deliberately unauthenticated) |
| GET | `/api/matches` | → `MatchSummaryBody[]` |
| POST | `/api/matches` | `CreateMatchBody` → 201 `CreatedMatchBody` (`match_id`, `stage_token`) |
| GET | `/api/matches/{id}` | → `SnapshotBody` (`frame`, `stage_token`) |
| POST | `/api/matches/{id}/players` | `AddPlayerBody` → `OutcomeBody` |
| POST | `/api/matches/{id}/secrets` | `AssignSecretBody` → `OutcomeBody` |
| POST | `/api/matches/{id}/deal` | → `OutcomeBody` — repeatable; this *is* «перераздать» (§3.4) |
| POST | `/api/matches/{id}/start` | → `OutcomeBody` |
| GET | `/api/library/categories` | → `CategorySummaryBody[]` (active **and** inactive) |
| POST | `/api/library/categories` | `CreateCategoryBody` → 201 `CategorySummaryBody` |
| GET | `/api/library/categories/{id}` | → `CategoryDetailBody` |
| PUT | `/api/library/categories/{id}` | `EditCategoryBody` → `CategoryDetailBody` |
| PUT | `/api/library/categories/{id}/active` | `SetActiveBody` → `CategoryDetailBody` |
| POST | `/api/library/categories/{id}/images` | `AddImageBody` → 201 `ImageBody` |
| PUT | `/api/library/images/{id}` | `EditImageBody` → `ImageBody` |
| PUT | `/api/library/images/{id}/active` | `SetActiveBody` → `ImageBody` |
| PUT | `/api/library/categories/{id}/images/order` | `ReorderImagesBody` → `CategoryDetailBody` |
| GET | `/api/library/readiness?cells=N` | → `ReadinessBody` |
| POST | `/api/media` | raw bytes, `content-type` set → 201 `UploadedMediaBody` |
| GET | `/api/media/{digest}` | → the bytes (no auth) |
| WS | `/ws/host/{match_id}` | `Envelope` in → `Ack` out; `HostFrame` pushed on every change |

The socket multiplexes two message kinds on one channel, discriminated by `kind`: `"host"` is a `HostFrame`, `"ack"` is an `Ack`. Close codes: **1008** means unauthenticated, **1011** means the match could not be loaded. Neither is worth retrying blindly.

---

## File Structure

New and changed only; everything the stage plan built stays as it is.

```
frontend/src/
  app/
    providers.tsx                    QueryClientProvider
    main.tsx                         MODIFIED: wrap the router
    routes/
      host.tsx                       layout: auth gate + chrome
      host.index.tsx                 match list
      host.library.tsx               the library screen
      host.match.$matchId.tsx        the live console
  pages/
    host-login/     ui/login-page.tsx
    host-home/      ui/home-page.tsx
    host-library/   ui/library-page.tsx
    host-match/     ui/match-page.tsx          picks setup / board / judging
  widgets/
    host-board/      ui/host-board.tsx         all categories, click targets, dimming
    category-editor/ ui/category-editor.tsx    one category, its images, upload
    match-setup/     ui/match-setup.tsx        players, colours, secrets, deal
    judging/         ui/judging-panel.tsx      timers, answer, thumbnail, buttons
  entities/
    board/          lib/geometry.ts            MOVED from widgets/board/lib
    match/          model/host-beat.ts         which console screen a frame wants
  features/
    session/         api/use-session.ts        login, logout, the auth probe
    library/         api/use-library.ts        every library query and mutation
    match-assembly/  api/use-assembly.ts       create, players, secrets, deal, start
    host-commands/   api/use-host-match.ts     the socket: frames in, commands out
                     model/selection.ts        zustand: the selected group
  shared/
    api/host-socket.ts                         connect, send, acks
    lib/use-hotkeys.ts                         window-level key handling
frontend/testing/
    server.ts                                  MSW
    query.tsx                                  renderWithQuery / withQuery
    host-frames.ts                             HostFrame builders
frontend/scripts/
    assert-route-split.mjs                     H8's build assertion
```

`features/` is a new FSD layer for this plan: each slice is one thing the operator can *do*, and each owns its own server calls. That is what keeps `pages/` thin enough to read.

---

## Rulings

**H1 — The console never optimistically updates.** A command is submitted, the server acks it, and the *next frame* is what the screen shows. No local mutation of match state, ever. §7.2 already makes every frame carry the whole state, and §9.2's tempo — one decision per five seconds — is far slower than a LAN round trip. *Cost if wrong:* on a congested network a judgement appears to lag by one round trip. The alternative is worse: an optimistic screen that showed a judgement the server rejected would have the operator narrating a score the game does not have.

**H2 — Illegal attacks are made impossible, not validated.** §9.2 says so in as many words: «Правило смежности не проверяется, а делается невозможным». `HostFrame.legal_attacks` is `Record<attackingGroupId, defendingGroupId[]>`; the console derives every click target from it — a group that is not a key cannot be selected, and a target not in its list is not a button. There is no branch anywhere that says "this attack is illegal". *Cost if wrong:* if `legal_attacks` is ever empty the operator is stuck with nothing clickable rather than getting a rejection they could read. That is the right failure: a stuck console is visible, a wrong attack is not.

**H3 — Board geometry moves to `entities/board`; the two boards stay separate widgets.** The stage board and the host board differ in almost everything visible — the host shows every category including other players' secrets, dims illegal targets, and takes clicks — so one widget with a `mode` prop would branch in every function. But `outlinePath` and `labelAnchor` are pure maths both need, and FSD forbids a widget importing a widget. So the maths moves down a layer. *Cost if wrong:* one more file and one changed import. Reversible in ten minutes.

**H4 — Keyboard handlers key off `event.code`, not `event.key`.** §9.2 assigns `P` to «пас». On a Russian keyboard layout the physical P key reports `event.key === "з"`, and the operator running a Russian-language show is exactly who will have that layout active. `event.code === "KeyP"` is the physical key regardless of layout. *Cost if wrong:* the primary input for a game played at one decision per five seconds silently stops working for the operator most likely to be using it — and it would pass every test written on a US layout.

**H5 — Hotkeys never fire while a text field has focus.** The library screen has title and answer inputs; Space in one of them must type a space. The handler bails when the active element is an `input`, `textarea`, `select` or `[contenteditable]`. *Cost if wrong:* an operator typing an answer would judge the live duel by pressing the space bar.

**H6 — TanStack Query owns the REST surface; the socket owns the match.** Query gives caching, invalidation-on-mutation and request dedup for the library and assembly screens, which are ordinary CRUD. It is deliberately *not* used for match state: a query cache holding a frame that a socket also updates would be two sources of truth for one thing, which is what H1 exists to prevent. The one exception is the initial snapshot (`GET /api/matches/{id}`), fetched **only** for the `stage_token` — which the socket never carries. *Cost if wrong:* a stale library list after an edit in another tab, fixed by a manual invalidate.

**H7 — Zustand holds exactly one thing: the group the operator has selected.** It is client-only, transient, belongs to no frame, and is read by two components that are not parent and child. Everything else on this surface is either server state (Query, socket) or local component state. *Cost if wrong:* a store that grows into a second copy of match state — which is why Task 7 adds a test asserting the store's keys never grew.

**H8 — The two route trees must not share a bundle.** §9 asks for «ленивые деревья роутов»: the projector in the hall must not download the console that carries every answer. `autoCodeSplitting` already emits separate chunks; Task 7 adds a build assertion so a careless shared import fails CI instead of shipping. *Cost if wrong:* the stage screen downloads answers it never renders — not a leak to the audience, but one refresh of the projector's devtools away from being one.

**H9 — Media upload is two calls, and the digest is the join.** `POST /api/media` stores the bytes and returns `media_sha256`; `POST /api/library/categories/{id}/images` then records the row against that digest. The console never computes a digest itself, and the server refuses one it has not stored. *Cost if wrong:* nothing — the server rejects the second call.

---

## Task 1: The host shell — session, Query, and the route tree

Login, logout, the authenticated layout, and the match list. Nothing here talks to a match.

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/src/app/providers.tsx`; Modify: `frontend/src/app/main.tsx`
- Create: `frontend/src/features/session/api/use-session.ts`, `.../api/use-session.test.tsx`, `.../index.ts`
- Create: `frontend/src/pages/host-login/ui/login-page.tsx`, `.../ui/login-page.test.tsx`, `.../index.ts`
- Create: `frontend/src/pages/host-home/ui/home-page.tsx`, `.../ui/home-page.test.tsx`, `.../index.ts`
- Create: `frontend/src/app/routes/host.tsx`, `host.index.tsx`, `host.library.tsx` (stub), `host.match.$matchId.tsx` (stub)
- Create: `frontend/testing/query.tsx`, `frontend/testing/server.ts`
- Modify: `frontend/src/app/routes/index.tsx`, `frontend/testing/setup.ts`

**Interfaces:**
- Produces:
  - `useLogin(): { logIn(password: string): Promise<boolean>; pending: boolean; failed: boolean }`
  - `useAuthGate(): { state: "checking" | "in" | "out" }`
  - `useLogout(): () => Promise<void>`
  - `<LoginPage />`, `<HomePage />`
  - `renderWithQuery(ui)` and `withQuery()` from `testing/query`; `server` from `testing/server`

- [x] **Step 1: Add the dependencies**

```bash
cd frontend && pnpm add @tanstack/react-query zustand && pnpm add -D msw
```

The stage plan's R6 deferred these deliberately; this is the plan that needs them.

- [x] **Step 2: Write `frontend/testing/server.ts`**

```ts
import { setupServer } from "msw/node";

/** No default handlers: every test declares the responses it depends on,
 * so a test that forgot one fails loudly rather than passing against a
 * fixture written for a different test. */
export const server = setupServer();
```

- [x] **Step 3: Extend `frontend/testing/setup.ts`**

Append, keeping everything already there:

```ts
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./server";

// `onUnhandledRequest: "error"` is the point: an unmocked call is a test
// silently exercising nothing.
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
```

- [x] **Step 4: Write `frontend/testing/query.tsx`**

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type RenderResult, render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";

// Retries off and no cache between tests: a retrying query turns an
// assertion about one request into a five-second timeout.
function client(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
}

export function renderWithQuery(ui: ReactElement): RenderResult {
  const query = client();
  return render(<QueryClientProvider client={query}>{ui}</QueryClientProvider>);
}

/** `renderHook` needs a wrapper component rather than a render call. */
export function withQuery() {
  const query = client();
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={query}>{children}</QueryClientProvider>;
  };
}
```

- [x] **Step 5: Write `frontend/src/app/providers.tsx`**

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// One client for the application's lifetime. `staleTime` is generous
// because the library is edited by one operator on one machine (§1.1) —
// there is no second writer to race.
const client = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: false } },
});

export function Providers({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
```

- [x] **Step 6: Wrap the router in `frontend/src/app/main.tsx`**

Add `import { Providers } from "./providers";` and change only the render call:

```tsx
createRoot(root).render(
  <StrictMode>
    <Providers>
      <RouterProvider router={router} />
    </Providers>
  </StrictMode>,
);
```

- [x] **Step 7: Write the failing session test — `frontend/src/features/session/api/use-session.test.tsx`**

```tsx
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { withQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { useAuthGate, useLogin, useLogout } from "./use-session";

describe("useLogin", () => {
  it("reports success when the server sets a cookie", async () => {
    server.use(http.post("/api/session", () => new HttpResponse(null, { status: 204 })));
    const { result } = renderHook(() => useLogin(), { wrapper: withQuery() });
    let accepted = false;
    await act(async () => {
      accepted = await result.current.logIn("hunter2");
    });
    expect(accepted).toBe(true);
  });

  it("reports a wrong password without throwing", async () => {
    // §7.5's 401. Kills on: letting the rejected promise escape, which
    // puts an unhandled error in front of an operator who simply mistyped.
    server.use(http.post("/api/session", () => new HttpResponse(null, { status: 401 })));
    const { result } = renderHook(() => useLogin(), { wrapper: withQuery() });
    let accepted = true;
    await act(async () => {
      accepted = await result.current.logIn("wrong");
    });
    expect(accepted).toBe(false);
    await waitFor(() => expect(result.current.failed).toBe(true));
  });
});

describe("useAuthGate", () => {
  it("is in when the probe succeeds", async () => {
    server.use(http.get("/api/matches", () => HttpResponse.json([])));
    const { result } = renderHook(() => useAuthGate(), { wrapper: withQuery() });
    await waitFor(() => expect(result.current.state).toBe("in"));
  });

  it("is out when the probe is refused", async () => {
    // The cookie is HttpOnly (§7.5), so the client cannot read it. Asking
    // the server is the only honest test. Kills on: assuming a session
    // exists because one was created earlier in this tab.
    server.use(http.get("/api/matches", () => new HttpResponse(null, { status: 401 })));
    const { result } = renderHook(() => useAuthGate(), { wrapper: withQuery() });
    await waitFor(() => expect(result.current.state).toBe("out"));
  });
});

describe("useLogout", () => {
  it("asks the server and then reports the session gone", async () => {
    server.use(
      http.get("/api/matches", () => HttpResponse.json([])),
      http.delete("/api/session", () => new HttpResponse(null, { status: 204 })),
    );
    const { result } = renderHook(() => ({ gate: useAuthGate(), out: useLogout() }), {
      wrapper: withQuery(),
    });
    await waitFor(() => expect(result.current.gate.state).toBe("in"));
    server.use(http.get("/api/matches", () => new HttpResponse(null, { status: 401 })));
    await act(async () => {
      await result.current.out();
    });
    await waitFor(() => expect(result.current.gate.state).toBe("out"));
  });
});
```

- [x] **Step 8: Run it and watch it fail**

```bash
cd frontend && pnpm vitest run src/features/session; echo "exit=$?"
```

Expected: FAIL — `Failed to resolve import "./use-session"`.

- [x] **Step 9: Write `frontend/src/features/session/api/use-session.ts`**

```ts
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
    // Everything behind the session is now unreadable; dropping the whole
    // cache is simpler and safer than naming each key.
    client.clear();
    await client.invalidateQueries({ queryKey: AUTH_PROBE });
  }, [client]);
}
```

`frontend/src/features/session/index.ts`:

```ts
export { AUTH_PROBE, useAuthGate, useLogin, useLogout } from "./api/use-session";
```

- [x] **Step 10: Run it and watch it pass**

Expected: PASS, 5 tests.

- [x] **Step 11: Write the failing login-page test — `frontend/src/pages/host-login/ui/login-page.test.tsx`**

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { renderWithQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { LoginPage } from "./login-page";

describe("LoginPage", () => {
  it("submits the password the operator typed", async () => {
    const seen: string[] = [];
    server.use(
      http.post("/api/session", async ({ request }) => {
        seen.push(((await request.json()) as { password: string }).password);
        return new HttpResponse(null, { status: 204 });
      }),
      http.get("/api/matches", () => HttpResponse.json([])),
    );
    renderWithQuery(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Пароль"), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    await waitFor(() => expect(seen).toEqual(["hunter2"]));
  });

  it("says the password was wrong, and keeps the field usable", async () => {
    // Kills on: clearing the form or unmounting it on a 401 — the
    // operator retypes on a screen that just lost their input.
    server.use(http.post("/api/session", () => new HttpResponse(null, { status: 401 })));
    renderWithQuery(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Пароль"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByText("Неверный пароль")).toBeInTheDocument();
    expect(screen.getByLabelText("Пароль")).toBeEnabled();
  });

  it("does not put the password on screen as readable text", () => {
    // The console is often operated with a room watching the screen.
    renderWithQuery(<LoginPage />);
    expect(screen.getByLabelText("Пароль")).toHaveAttribute("type", "password");
  });
});
```

- [x] **Step 12: Run it, watch it fail, then write `frontend/src/pages/host-login/ui/login-page.tsx`**

```tsx
import { type FormEvent, useState } from "react";
import { useLogin } from "@/features/session";

export function LoginPage() {
  const { logIn, pending, failed } = useLogin();
  const [password, setPassword] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    await logIn(password);
  }

  return (
    <main className="flex h-full items-center justify-center">
      <form onSubmit={submit} className="flex w-80 flex-col gap-4">
        <h1 className="font-display text-4xl uppercase">budge</h1>
        <label className="flex flex-col gap-2 text-sm text-stage-muted">
          Пароль
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="rounded-lg bg-white/10 px-4 py-3 text-lg text-stage-ink outline-none focus:bg-white/15"
          />
        </label>
        {failed && <p className="text-red-400">Неверный пароль</p>}
        <button
          type="submit"
          disabled={pending}
          className="rounded-lg bg-white/15 px-4 py-3 font-display text-xl uppercase disabled:opacity-50"
        >
          Войти
        </button>
      </form>
    </main>
  );
}
```

`frontend/src/pages/host-login/index.ts`: `export { LoginPage } from "./ui/login-page";`

The `<label>` wraps the input, so `getByLabelText("Пароль")` resolves without an explicit `htmlFor`/`id` pair.

- [x] **Step 13: Run it and watch it pass**

Expected: PASS, 3 tests.

- [x] **Step 14: Write the failing home-page test — `frontend/src/pages/host-home/ui/home-page.test.tsx`**

```tsx
import { screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { renderWithQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { HomePage } from "./home-page";

const MATCH = {
  id: "33333333-3333-3333-3333-333333333333",
  status: "running",
  winner_id: null,
  last_seq: 12,
  players: [
    { name: "Аня", colour: "#e4572e", eliminated: false },
    { name: "Борис", colour: "#2e86e4", eliminated: true },
  ],
};

describe("HomePage", () => {
  it("lists the matches the server knows about", async () => {
    server.use(http.get("/api/matches", () => HttpResponse.json([MATCH])));
    renderWithQuery(<HomePage />);
    expect(await screen.findByText("Аня")).toBeInTheDocument();
    expect(screen.getByText("Борис")).toBeInTheDocument();
  });

  it("says so plainly when there are none", async () => {
    // Kills on: an empty list rendering as blank space, which reads as a
    // broken screen rather than a fresh install.
    server.use(http.get("/api/matches", () => HttpResponse.json([])));
    renderWithQuery(<HomePage />);
    expect(await screen.findByText("Партий пока нет")).toBeInTheDocument();
  });

  it("marks an eliminated player as out", async () => {
    server.use(http.get("/api/matches", () => HttpResponse.json([MATCH])));
    renderWithQuery(<HomePage />);
    expect(await screen.findByTestId("player-Борис")).toHaveAttribute("data-eliminated", "true");
    expect(screen.getByTestId("player-Аня")).toHaveAttribute("data-eliminated", "false");
  });
});
```

- [x] **Step 15: Write the two route stubs first**

`HomePage` links to routes that do not exist yet, and TanStack Router's `Link` is typed against the generated tree — it will not compile without them. Create both now as stubs; Tasks 2 and 6 replace them.

`frontend/src/app/routes/host.library.tsx`:

```tsx
import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/host/library")({ component: () => null });
```

`frontend/src/app/routes/host.match.$matchId.tsx`:

```tsx
import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/host/match/$matchId")({ component: () => null });
```

- [x] **Step 16: Run the home-page test, watch it fail, then write `frontend/src/pages/host-home/ui/home-page.tsx`**

```tsx
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import type { MatchSummaryBody } from "@/shared/api";

export function HomePage() {
  const matches = useQuery({
    queryKey: ["matches"],
    queryFn: async (): Promise<MatchSummaryBody[]> => {
      const response = await fetch("/api/matches");
      if (!response.ok) throw new Error(`matches: ${response.status}`);
      return await response.json();
    },
  });

  return (
    <section className="flex flex-col gap-6 p-8">
      <div className="flex items-center justify-between">
        <h2 className="font-display text-3xl uppercase">Партии</h2>
        <Link to="/host/library" className="text-stage-muted underline">
          Библиотека
        </Link>
      </div>
      {matches.data?.length === 0 && <p className="text-stage-muted">Партий пока нет</p>}
      <ul className="flex flex-col gap-3">
        {matches.data?.map((match) => (
          <li key={match.id} className="rounded-xl bg-white/5 p-4">
            <Link to="/host/match/$matchId" params={{ matchId: match.id }} className="flex gap-4">
              <span className="font-display uppercase">{match.status}</span>
              <span className="flex gap-3">
                {match.players.map((person) => (
                  <span
                    key={person.name}
                    data-testid={`player-${person.name}`}
                    data-eliminated={person.eliminated}
                    style={{ color: person.colour }}
                    className="data-[eliminated=true]:line-through data-[eliminated=true]:opacity-50"
                  >
                    {person.name}
                  </span>
                ))}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
```

`frontend/src/pages/host-home/index.ts`: `export { HomePage } from "./ui/home-page";`

- [x] **Step 17: Write the layout route — `frontend/src/app/routes/host.tsx`**

The auth gate lives here so every child inherits it, and a 401 anywhere lands on the login screen rather than an empty page.

```tsx
import { Outlet, createFileRoute } from "@tanstack/react-router";
import { useAuthGate, useLogout } from "@/features/session";
import { LoginPage } from "@/pages/host-login";

export const Route = createFileRoute("/host")({ component: HostLayout });

function HostLayout() {
  const { state } = useAuthGate();
  const logOut = useLogout();

  if (state === "checking") {
    return <main className="flex h-full items-center justify-center text-stage-muted">…</main>;
  }
  if (state === "out") return <LoginPage />;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-white/10 border-b px-8 py-4">
        <span className="font-display text-2xl uppercase">budge</span>
        <button
          type="button"
          onClick={() => void logOut()}
          className="text-sm text-stage-muted underline"
        >
          Выйти
        </button>
      </header>
      <div className="min-h-0 flex-1 overflow-auto">
        <Outlet />
      </div>
    </div>
  );
}
```

`frontend/src/app/routes/host.index.tsx`:

```tsx
import { createFileRoute } from "@tanstack/react-router";
import { HomePage } from "@/pages/host-home";

export const Route = createFileRoute("/host/")({ component: HomePage });
```

- [x] **Step 18: Point `/` at the console — `frontend/src/app/routes/index.tsx`**

Replaces the stage plan's placeholder:

```tsx
import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/")({
  beforeLoad: () => {
    throw redirect({ to: "/host" });
  },
});
```

- [x] **Step 19: Regenerate the tree, then green everything**

```bash
cd frontend && pnpm exec vite build; echo "exit=$?"
pnpm build; echo "exit=$?"
pnpm check; echo "exit=$?"
pnpm test; echo "exit=$?"
```

Expected: all 0. The `pnpm exec vite build` first is mandatory — five route files changed, and `tsc` runs before the router plugin.

- [x] **Step 20: Commit**

```bash
cd .. && git add frontend && git commit -m "feat(host): the console shell — session, query, and the route tree"
```

---

## Task 2: The library screen

§9.2's «отбор категорий из библиотеки» and §8's content administration: categories, their images, upload, soft delete, and the readiness warning.

**Files:**
- Create: `frontend/src/features/library/api/use-library.ts`, `.../api/use-library.test.tsx`, `.../index.ts`
- Create: `frontend/src/widgets/category-editor/ui/category-editor.tsx`, `.../ui/category-editor.test.tsx`, `.../index.ts`
- Create: `frontend/src/pages/host-library/ui/library-page.tsx`, `.../ui/library-page.test.tsx`, `.../index.ts`
- Modify: `frontend/src/app/routes/host.library.tsx`

**Interfaces:**
- Produces: `useCategories()`, `useCategory(id)`, `useReadiness(cells)`, `useCreateCategory()`, `useEditCategory()`, `useSetCategoryActive()`, `useAddImage()`, `useEditImage()`, `useSetImageActive()`, `useReorderImages()`, `useUploadMedia()`, `<CategoryEditor categoryId />`, `<LibraryPage />`

- [x] **Step 1: Write the failing library-api test — `frontend/src/features/library/api/use-library.test.tsx`**

```tsx
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { withQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { useAddImage, useCategories, useCategory, useReadiness, useUploadMedia } from "./use-library";

const ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const CATEGORY = {
  id: ID,
  title: "Кино",
  is_secret: false,
  is_active: true,
  version: 3,
  active_image_count: 12,
};

describe("useCategories", () => {
  it("returns inactive categories too", async () => {
    // §5.3: content is soft-deleted. An operator who switched a theme off
    // has to see it to switch it back on. Kills on: filtering here.
    server.use(
      http.get("/api/library/categories", () =>
        HttpResponse.json([CATEGORY, { ...CATEGORY, id: "b", title: "Спорт", is_active: false }]),
      ),
    );
    const { result } = renderHook(() => useCategories(), { wrapper: withQuery() });
    await waitFor(() => expect(result.current.data).toHaveLength(2));
    expect(result.current.data?.map((row) => row.title)).toEqual(["Кино", "Спорт"]);
  });
});

describe("useReadiness", () => {
  it("asks about the board it was given, not a default", async () => {
    // §8's soft check is answered per board size; asking about the wrong
    // number tells the operator they are ready when they are not.
    const asked: string[] = [];
    server.use(
      http.get("/api/library/readiness", ({ request }) => {
        asked.push(new URL(request.url).searchParams.get("cells") ?? "");
        return HttpResponse.json({
          cells: 12,
          threshold: 5,
          ordinary_available: 40,
          secrets_available: 6,
          thin: [],
          ready: true,
        });
      }),
    );
    renderHook(() => useReadiness(12), { wrapper: withQuery() });
    await waitFor(() => expect(asked).toEqual(["12"]));
  });
});

describe("useUploadMedia", () => {
  it("posts the bytes and hands back the digest the server computed", async () => {
    // §7.6 and H9: the digest is the join between the two calls, and the
    // console never invents one.
    server.use(
      http.post("/api/media", () =>
        HttpResponse.json(
          { media_sha256: "c".repeat(64), content_type: "image/png", bytes: 4 },
          { status: 201 },
        ),
      ),
    );
    const { result } = renderHook(() => useUploadMedia(), { wrapper: withQuery() });
    let digest = "";
    await act(async () => {
      const file = new File([new Uint8Array([1, 2, 3, 4])], "x.png", { type: "image/png" });
      digest = (await result.current(file)).media_sha256;
    });
    expect(digest).toBe("c".repeat(64));
  });
});

describe("useAddImage", () => {
  it("refetches the category it changed", async () => {
    // Kills on: a mutation that does not invalidate — the operator adds a
    // picture, the list does not change, so they add it again.
    let reads = 0;
    server.use(
      http.get(`/api/library/categories/${ID}`, () => {
        reads += 1;
        return HttpResponse.json({ category: CATEGORY, images: [] });
      }),
      http.post(`/api/library/categories/${ID}/images`, () =>
        HttpResponse.json(
          {
            id: "i1",
            media_sha256: "c".repeat(64),
            answer_text: "Титаник",
            position: 0,
            is_active: true,
          },
          { status: 201 },
        ),
      ),
    );
    const { result } = renderHook(() => ({ add: useAddImage(), detail: useCategory(ID) }), {
      wrapper: withQuery(),
    });
    await waitFor(() => expect(reads).toBe(1));
    await act(async () => {
      await result.current.add.mutateAsync({
        categoryId: ID,
        media_sha256: "c".repeat(64),
        answer_text: "Титаник",
      });
    });
    await waitFor(() => expect(reads).toBe(2));
  });
});
```

- [x] **Step 2: Run it and watch it fail**

```bash
cd frontend && pnpm vitest run src/features/library; echo "exit=$?"
```

Expected: FAIL — `Failed to resolve import "./use-library"`.

- [x] **Step 3: Write `frontend/src/features/library/api/use-library.ts`**

```ts
import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback } from "react";
import type {
  CategoryDetailBody,
  CategorySummaryBody,
  ImageBody,
  ReadinessBody,
  UploadedMediaBody,
} from "@/shared/api";

const LIBRARY = "library";

async function json<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  if (!response.ok) throw new Error(`${input}: ${response.status}`);
  return (await response.json()) as T;
}

function payload(value: unknown): RequestInit {
  return { headers: { "content-type": "application/json" }, body: JSON.stringify(value) };
}

export function useCategories(): UseQueryResult<CategorySummaryBody[]> {
  return useQuery({
    queryKey: [LIBRARY, "categories"],
    queryFn: () => json<CategorySummaryBody[]>("/api/library/categories"),
  });
}

export function useCategory(categoryId: string): UseQueryResult<CategoryDetailBody> {
  return useQuery({
    queryKey: [LIBRARY, "category", categoryId],
    queryFn: () => json<CategoryDetailBody>(`/api/library/categories/${categoryId}`),
  });
}

export function useReadiness(cells: number): UseQueryResult<ReadinessBody> {
  return useQuery({
    queryKey: [LIBRARY, "readiness", cells],
    queryFn: () => json<ReadinessBody>(`/api/library/readiness?cells=${cells}`),
  });
}

/** Every mutation invalidates the whole `library` key rather than naming
 * what it touched. The library is small, one operator edits it, and a
 * missed invalidation is a screen that lies. */
function useLibraryMutation<TArgs, TResult>(
  run: (args: TArgs) => Promise<TResult>,
): UseMutationResult<TResult, Error, TArgs> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: [LIBRARY] });
    },
  });
}

export function useCreateCategory() {
  return useLibraryMutation(({ title, is_secret }: { title: string; is_secret: boolean }) =>
    json<CategorySummaryBody>("/api/library/categories", {
      method: "POST",
      ...payload({ title, is_secret }),
    }),
  );
}

export function useEditCategory() {
  return useLibraryMutation(
    ({ categoryId, title, is_secret }: { categoryId: string; title: string; is_secret: boolean }) =>
      json<CategoryDetailBody>(`/api/library/categories/${categoryId}`, {
        method: "PUT",
        ...payload({ title, is_secret }),
      }),
  );
}

export function useSetCategoryActive() {
  return useLibraryMutation(
    ({ categoryId, is_active }: { categoryId: string; is_active: boolean }) =>
      json<CategoryDetailBody>(`/api/library/categories/${categoryId}/active`, {
        method: "PUT",
        ...payload({ is_active }),
      }),
  );
}

export function useAddImage() {
  return useLibraryMutation(
    (args: { categoryId: string; media_sha256: string; answer_text: string }) =>
      json<ImageBody>(`/api/library/categories/${args.categoryId}/images`, {
        method: "POST",
        ...payload({ media_sha256: args.media_sha256, answer_text: args.answer_text }),
      }),
  );
}

export function useEditImage() {
  return useLibraryMutation(
    (args: { imageId: string; media_sha256: string; answer_text: string }) =>
      json<ImageBody>(`/api/library/images/${args.imageId}`, {
        method: "PUT",
        ...payload({ media_sha256: args.media_sha256, answer_text: args.answer_text }),
      }),
  );
}

export function useSetImageActive() {
  return useLibraryMutation(({ imageId, is_active }: { imageId: string; is_active: boolean }) =>
    json<ImageBody>(`/api/library/images/${imageId}/active`, {
      method: "PUT",
      ...payload({ is_active }),
    }),
  );
}

export function useReorderImages() {
  return useLibraryMutation(
    ({ categoryId, image_ids }: { categoryId: string; image_ids: string[] }) =>
      json<CategoryDetailBody>(`/api/library/categories/${categoryId}/images/order`, {
        method: "PUT",
        ...payload({ image_ids }),
      }),
  );
}

/** H9: store the bytes, get the digest back. The image row is a second
 * call, and the server refuses a digest it has not stored. */
export function useUploadMedia(): (file: File) => Promise<UploadedMediaBody> {
  return useCallback(async (file: File) => {
    const response = await fetch("/api/media", {
      method: "POST",
      headers: { "content-type": file.type },
      body: file,
    });
    if (!response.ok) throw new Error(`upload: ${response.status}`);
    return (await response.json()) as UploadedMediaBody;
  }, []);
}
```

`frontend/src/features/library/index.ts` re-exports all eleven names.

- [x] **Step 4: Run it and watch it pass**

Expected: PASS, 4 tests.

- [x] **Step 5: Write the failing editor test — `frontend/src/widgets/category-editor/ui/category-editor.test.tsx`**

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { renderWithQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { CategoryEditor } from "./category-editor";

const ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const DETAIL = {
  category: {
    id: ID,
    title: "Кино",
    is_secret: false,
    is_active: true,
    version: 3,
    active_image_count: 2,
  },
  images: [
    { id: "i1", media_sha256: "a".repeat(64), answer_text: "Титаник", position: 0, is_active: true },
    { id: "i2", media_sha256: "b".repeat(64), answer_text: "Аватар", position: 1, is_active: false },
  ],
};

describe("CategoryEditor", () => {
  it("shows each image's answer and its picture", async () => {
    server.use(http.get(`/api/library/categories/${ID}`, () => HttpResponse.json(DETAIL)));
    renderWithQuery(<CategoryEditor categoryId={ID} />);
    expect(await screen.findByDisplayValue("Титаник")).toBeInTheDocument();
    expect(screen.getByAltText("Титаник")).toHaveAttribute("src", `/api/media/${"a".repeat(64)}`);
  });

  it("marks a soft-deleted image without hiding it", async () => {
    // §5.3: soft delete has an undo, and an invisible row cannot be undone.
    server.use(http.get(`/api/library/categories/${ID}`, () => HttpResponse.json(DETAIL)));
    renderWithQuery(<CategoryEditor categoryId={ID} />);
    expect(await screen.findByTestId("image-i2")).toHaveAttribute("data-active", "false");
    expect(screen.getByTestId("image-i1")).toHaveAttribute("data-active", "true");
  });

  it("uploads the file first and records the digest it got back", async () => {
    // H9. Kills on: sending the filename, or a digest computed in the
    // browser — the server refuses a digest it has not stored, and the
    // operator would see an upload that silently added nothing.
    const recorded: unknown[] = [];
    server.use(
      http.get(`/api/library/categories/${ID}`, () => HttpResponse.json(DETAIL)),
      http.post("/api/media", () =>
        HttpResponse.json(
          { media_sha256: "c".repeat(64), content_type: "image/png", bytes: 4 },
          { status: 201 },
        ),
      ),
      http.post(`/api/library/categories/${ID}/images`, async ({ request }) => {
        recorded.push(await request.json());
        return HttpResponse.json(
          { id: "i3", media_sha256: "c".repeat(64), answer_text: "", position: 2, is_active: true },
          { status: 201 },
        );
      }),
    );
    renderWithQuery(<CategoryEditor categoryId={ID} />);
    await screen.findByDisplayValue("Титаник");
    await userEvent.upload(
      screen.getByLabelText("Добавить картинку"),
      new File([new Uint8Array([1, 2, 3, 4])], "x.png", { type: "image/png" }),
    );
    await waitFor(() =>
      expect(recorded).toEqual([{ media_sha256: "c".repeat(64), answer_text: "" }]),
    );
  });

  it("saves an answer typed against a picture", async () => {
    // An image uploaded with an empty answer is useless until this
    // works. Kills on: an input with no save path — the operator types
    // the answer, leaves the screen, and it is gone.
    const saved: unknown[] = [];
    server.use(
      http.get(`/api/library/categories/${ID}`, () => HttpResponse.json(DETAIL)),
      http.put("/api/library/images/i1", async ({ request }) => {
        saved.push(await request.json());
        return HttpResponse.json(DETAIL.images[0]);
      }),
    );
    renderWithQuery(<CategoryEditor categoryId={ID} />);
    const answer = await screen.findByLabelText("Ответ i1");
    await userEvent.clear(answer);
    await userEvent.type(answer, "Аватар");
    await userEvent.tab();
    await waitFor(() =>
      expect(saved).toEqual([{ media_sha256: "a".repeat(64), answer_text: "Аватар" }]),
    );
  });

  it("renames the category through the edit route", async () => {
    const sent: unknown[] = [];
    server.use(
      http.get(`/api/library/categories/${ID}`, () => HttpResponse.json(DETAIL)),
      http.put(`/api/library/categories/${ID}`, async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json(DETAIL);
      }),
    );
    renderWithQuery(<CategoryEditor categoryId={ID} />);
    const title = await screen.findByLabelText("Название");
    await userEvent.clear(title);
    await userEvent.type(title, "Музыка");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(sent).toEqual([{ title: "Музыка", is_secret: false }]));
  });
});
```

- [x] **Step 6: Run it, watch it fail, then write `frontend/src/widgets/category-editor/ui/category-editor.tsx`**

```tsx
import { type ChangeEvent, useEffect, useState } from "react";
import {
  useAddImage,
  useCategory,
  useEditCategory,
  useEditImage,
  useSetImageActive,
  useUploadMedia,
} from "@/features/library";
import { mediaUrl } from "@/shared/config";

export function CategoryEditor({ categoryId }: { categoryId: string }) {
  const detail = useCategory(categoryId);
  const edit = useEditCategory();
  const addImage = useAddImage();
  const editImage = useEditImage();
  const setImageActive = useSetImageActive();
  const upload = useUploadMedia();

  const [title, setTitle] = useState("");
  const [isSecret, setIsSecret] = useState(false);

  // The form mirrors the server's value until the operator edits it, and
  // §5.3's `version` is the honest signal that a different value arrived.
  const version = detail.data?.category.version;
  useEffect(() => {
    if (detail.data) {
      setTitle(detail.data.category.title);
      setIsSecret(detail.data.category.is_secret);
    }
  }, [version, detail.data]);

  async function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    const stored = await upload(file);
    await addImage.mutateAsync({ categoryId, media_sha256: stored.media_sha256, answer_text: "" });
    event.target.value = "";
  }

  if (!detail.data) return <p className="p-6 text-stage-muted">…</p>;

  return (
    <section className="flex flex-col gap-6 p-6">
      <div className="flex items-end gap-4">
        <label className="flex flex-col gap-1 text-sm text-stage-muted">
          Название
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            className="rounded-lg bg-white/10 px-3 py-2 text-lg text-stage-ink"
          />
        </label>
        <label className="flex items-center gap-2 pb-3 text-sm text-stage-muted">
          <input
            type="checkbox"
            checked={isSecret}
            onChange={(event) => setIsSecret(event.target.checked)}
          />
          Секретная
        </label>
        <button
          type="button"
          onClick={() => void edit.mutateAsync({ categoryId, title, is_secret: isSecret })}
          className="rounded-lg bg-white/15 px-4 py-2"
        >
          Сохранить
        </button>
      </div>

      <label className="flex w-fit cursor-pointer flex-col gap-1 text-sm text-stage-muted">
        Добавить картинку
        <input type="file" accept="image/*" onChange={onFile} className="text-stage-ink" />
      </label>

      <ul className="grid grid-cols-4 gap-4">
        {detail.data.images.map((image) => (
          <li
            key={image.id}
            data-testid={`image-${image.id}`}
            data-active={image.is_active}
            className="flex flex-col gap-2 rounded-xl bg-white/5 p-3 data-[active=false]:opacity-40"
          >
            <img
              src={mediaUrl(image.media_sha256)}
              alt={image.answer_text}
              className="h-32 w-full rounded-lg object-cover"
            />
            <input
              defaultValue={image.answer_text}
              aria-label={`Ответ ${image.id}`}
              onBlur={(event) => {
                // Saved on blur rather than per keystroke: the answer is
                // typed once, and §5.3 makes every edit bump the
                // category's version — one write per field, not one per
                // character.
                if (event.target.value === image.answer_text) return;
                void editImage.mutateAsync({
                  imageId: image.id,
                  media_sha256: image.media_sha256,
                  answer_text: event.target.value,
                });
              }}
              className="rounded bg-white/10 px-2 py-1 text-sm"
            />
            <button
              type="button"
              onClick={() =>
                void setImageActive.mutateAsync({ imageId: image.id, is_active: !image.is_active })
              }
              className="text-xs text-stage-muted underline"
            >
              {image.is_active ? "Выключить" : "Включить"}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
```

`frontend/src/widgets/category-editor/index.ts`: `export { CategoryEditor } from "./ui/category-editor";`

`alt={image.answer_text}` is what makes `getByAltText("Титаник")` resolve, and it is also correct: the answer *is* what the picture depicts.

- [x] **Step 7: Run it and watch it pass**

Expected: PASS, 5 tests.

- [x] **Step 8: Write the failing library-page test — `frontend/src/pages/host-library/ui/library-page.test.tsx`**

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { renderWithQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { LibraryPage } from "./library-page";

const CATEGORIES = [
  { id: "a", title: "Кино", is_secret: false, is_active: true, version: 1, active_image_count: 12 },
  { id: "b", title: "Тайна", is_secret: true, is_active: true, version: 1, active_image_count: 3 },
];

const READY = {
  cells: 12,
  threshold: 5,
  ordinary_available: 40,
  secrets_available: 6,
  thin: [],
  ready: true,
};

function stock(readiness: unknown = READY) {
  server.use(
    http.get("/api/library/categories", () => HttpResponse.json(CATEGORIES)),
    http.get("/api/library/readiness", () => HttpResponse.json(readiness)),
    http.get("/api/library/categories/:id", () =>
      HttpResponse.json({ category: CATEGORIES[0], images: [] }),
    ),
  );
}

describe("LibraryPage", () => {
  it("lists every category with its picture count", async () => {
    stock();
    renderWithQuery(<LibraryPage />);
    expect(await screen.findByText("Кино")).toBeInTheDocument();
    expect(screen.getByTestId("count-a")).toHaveTextContent("12");
  });

  it("marks which categories are secrets", async () => {
    // §2.3: a secret is a different kind of thing, and the operator picks
    // one per player. Kills on: rendering both alike.
    stock();
    renderWithQuery(<LibraryPage />);
    await screen.findByText("Тайна");
    expect(screen.getByTestId("category-b")).toHaveAttribute("data-secret", "true");
    expect(screen.getByTestId("category-a")).toHaveAttribute("data-secret", "false");
  });

  it("warns softly when the library is thin, and refuses nothing", async () => {
    // §8: «мягкое предупреждение». Kills on: turning this into a block —
    // an operator who wants the show to go on must be able to start it.
    stock({ ...READY, ready: false, thin: [{ id: "b", title: "Тайна", active_image_count: 3 }] });
    renderWithQuery(<LibraryPage />);
    await screen.findByText("Кино");
    expect(screen.getByTestId("readiness")).toHaveAttribute("data-ready", "false");
    expect(screen.getByTestId("readiness")).toHaveTextContent("Тайна");
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("opens a category for editing when it is picked", async () => {
    stock();
    renderWithQuery(<LibraryPage />);
    await userEvent.click(await screen.findByText("Кино"));
    await waitFor(() => expect(screen.getByLabelText("Название")).toBeInTheDocument());
  });

  it("creates a category from the form", async () => {
    const made: unknown[] = [];
    stock();
    server.use(
      http.post("/api/library/categories", async ({ request }) => {
        made.push(await request.json());
        return HttpResponse.json(CATEGORIES[0], { status: 201 });
      }),
    );
    renderWithQuery(<LibraryPage />);
    await userEvent.type(await screen.findByLabelText("Новая тема"), "Музыка");
    await userEvent.click(screen.getByRole("button", { name: "Создать" }));
    await waitFor(() => expect(made).toEqual([{ title: "Музыка", is_secret: false }]));
  });
});
```

- [x] **Step 9: Run it, watch it fail, then write `frontend/src/pages/host-library/ui/library-page.tsx`**

```tsx
import { useState } from "react";
import { useCategories, useCreateCategory, useReadiness } from "@/features/library";
import { CategoryEditor } from "@/widgets/category-editor";

// §8's readiness is answered per board size. Twelve cells is the board the
// setup screen offers first; the operator sees a real number either way,
// and nothing here refuses anything.
const DEFAULT_CELLS = 12;

export function LibraryPage() {
  const categories = useCategories();
  const readiness = useReadiness(DEFAULT_CELLS);
  const create = useCreateCategory();
  const [picked, setPicked] = useState<string | null>(null);
  const [title, setTitle] = useState("");

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-80 flex-col gap-4 overflow-auto border-white/10 border-r p-6">
        <h2 className="font-display text-2xl uppercase">Библиотека</h2>

        {readiness.data && (
          <p
            data-testid="readiness"
            data-ready={readiness.data.ready}
            className="rounded-lg bg-white/5 p-3 text-sm text-stage-muted data-[ready=false]:text-amber-300"
          >
            {readiness.data.ready
              ? `Тем достаточно: ${readiness.data.ordinary_available}`
              : `Мало картинок: ${readiness.data.thin.map((row) => row.title).join(", ") || "—"}`}
          </p>
        )}

        <ul className="flex flex-col gap-1">
          {categories.data?.map((category) => (
            <li key={category.id}>
              <button
                type="button"
                data-testid={`category-${category.id}`}
                data-secret={category.is_secret}
                onClick={() => setPicked(category.id)}
                className="flex w-full justify-between rounded px-2 py-1 text-left hover:bg-white/10 data-[secret=true]:italic"
              >
                <span className={category.is_active ? "" : "line-through opacity-50"}>
                  {category.title}
                </span>
                <span data-testid={`count-${category.id}`} className="text-stage-muted">
                  {category.active_image_count}
                </span>
              </button>
            </li>
          ))}
        </ul>

        <div className="mt-auto flex flex-col gap-2">
          <label className="flex flex-col gap-1 text-sm text-stage-muted">
            Новая тема
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              className="rounded-lg bg-white/10 px-3 py-2 text-stage-ink"
            />
          </label>
          <button
            type="button"
            onClick={() => {
              void create.mutateAsync({ title, is_secret: false });
              setTitle("");
            }}
            className="rounded-lg bg-white/15 px-4 py-2"
          >
            Создать
          </button>
        </div>
      </aside>

      <div className="min-h-0 flex-1 overflow-auto">
        {picked === null ? (
          <p className="p-6 text-stage-muted">Выберите тему слева</p>
        ) : (
          <CategoryEditor categoryId={picked} />
        )}
      </div>
    </div>
  );
}
```

`frontend/src/pages/host-library/index.ts`: `export { LibraryPage } from "./ui/library-page";`

- [x] **Step 10: Replace the route stub — `frontend/src/app/routes/host.library.tsx`**

```tsx
import { createFileRoute } from "@tanstack/react-router";
import { LibraryPage } from "@/pages/host-library";

export const Route = createFileRoute("/host/library")({ component: LibraryPage });
```

- [x] **Step 11: Green everything and commit**

```bash
cd frontend && pnpm build; echo "exit=$?"
pnpm check; echo "exit=$?"
pnpm test; echo "exit=$?"
cd .. && git add frontend && git commit -m "feat(host): the library screen — categories, pictures, soft delete"
```

---

## Task 3: Match assembly

§9.2's «Подготовка»: players and colours, each one's secret, deal and redeal, start. Everything here is REST (§7.4).

**Files:**
- Create: `frontend/testing/host-frames.ts`
- Create: `frontend/src/features/match-assembly/api/use-assembly.ts`, `.../api/use-assembly.test.tsx`, `.../index.ts`
- Create: `frontend/src/widgets/match-setup/ui/match-setup.tsx`, `.../ui/match-setup.test.tsx`, `.../index.ts`
- Modify: `frontend/src/pages/host-home/ui/home-page.tsx` and its test (match creation)

**Interfaces:**
- Produces: `useCreateMatch()`, `useAddPlayer()`, `useAssignSecret()`, `useDeal()`, `useStart()`, `<MatchSetup frame matchId stageToken />`, and the `hostFrame` / `hostDuel` / `hostGroup` / `timing` / `player` / `resolution` builders.

- [x] **Step 1: Write `frontend/testing/host-frames.ts`**

The host counterpart of the stage plan's `testing/frames.ts`, and the only place a `HostFrame` is built in tests — so a contract change breaks one file rather than a dozen.

```ts
import type {
  HostDuelFrame,
  HostFrame,
  HostGroupFrame,
  PlayerFrame,
  ResolutionFrame,
  TimingFrame,
} from "@/shared/api";

export const ATTACKER = "11111111-1111-1111-1111-111111111111";
export const DEFENDER = "22222222-2222-2222-2222-222222222222";

export function player(overrides: Partial<PlayerFrame> = {}): PlayerFrame {
  return { id: ATTACKER, name: "Аня", colour: "#e4572e", eliminated: false, ...overrides };
}

export function hostGroup(overrides: Partial<HostGroupFrame> = {}): HostGroupFrame {
  return {
    id: "g1",
    owner: ATTACKER,
    category: { id: "c1", name: "Кино" },
    cells: [{ col: 0, row: 0 }],
    revealed: true,
    ...overrides,
  };
}

export function timing(overrides: Partial<TimingFrame> = {}): TimingFrame {
  return {
    remaining_ms: { [ATTACKER]: 60_000, [DEFENDER]: 60_000 },
    answering: ATTACKER,
    anchor: "2026-08-23T20:00:00Z",
    paused: false,
    deadline_at: "2026-08-23T20:01:00Z",
    ...overrides,
  };
}

export function hostDuel(overrides: Partial<HostDuelFrame> = {}): HostDuelFrame {
  return {
    attacker: ATTACKER,
    defender: DEFENDER,
    attacking_group: "g1",
    defending_group: "g2",
    category: { id: "c1", name: "Кино" },
    image_order: ["a".repeat(64), "b".repeat(64)],
    index: 0,
    image_count: 2,
    current_answer: "Титаник",
    phase: "declared",
    timing: timing(),
    ...overrides,
  };
}

export function resolution(overrides: Partial<ResolutionFrame> = {}): ResolutionFrame {
  return {
    winner: ATTACKER,
    loser: DEFENDER,
    surviving_group: "g1",
    absorbed_group: "g2",
    absorbed_cells: [{ col: 1, row: 0 }],
    ...overrides,
  };
}

export function hostFrame(overrides: Partial<HostFrame> = {}): HostFrame {
  return {
    kind: "host",
    match_id: "33333333-3333-3333-3333-333333333333",
    seq: 7,
    server_now: "2026-08-23T20:00:00Z",
    status: "running",
    board: { width: 3, height: 3 },
    players: [player(), player({ id: DEFENDER, name: "Борис", colour: "#2e86e4" })],
    current_player: ATTACKER,
    round_no: 2,
    groups: [
      hostGroup(),
      hostGroup({
        id: "g2",
        owner: DEFENDER,
        cells: [{ col: 1, row: 0 }],
        category: { id: "c2", name: "Спорт" },
      }),
    ],
    duel: null,
    winner: null,
    last_event_types: [],
    resolution: null,
    legal_attacks: { g1: ["g2"], g2: ["g1"] },
    ...overrides,
  };
}
```

- [x] **Step 2: Write the failing assembly test — `frontend/src/features/match-assembly/api/use-assembly.test.tsx`**

```tsx
import { act, renderHook } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
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
    let answer = { outcome: "", reason: null as string | null | undefined };
    await act(async () => {
      answer = await result.current.mutateAsync({ matchId: MATCH });
    });
    expect(answer.outcome).toBe("rejected");
    expect(answer.reason).toBe("content_unavailable");
  });
});
```

- [x] **Step 3: Run it, watch it fail, then write `frontend/src/features/match-assembly/api/use-assembly.ts`**

```ts
import { type UseMutationResult, useMutation, useQueryClient } from "@tanstack/react-query";
import type { CreateMatchBody, CreatedMatchBody, OutcomeBody } from "@/shared/api";

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
    }: { matchId: string; player_id: string; name: string; colour: string }) =>
      outcome(`/api/matches/${matchId}/players`, post(rest)),
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
```

`frontend/src/features/match-assembly/index.ts` re-exports all five.

- [x] **Step 4: Run it and watch it pass**

Expected: PASS, 3 tests.

- [x] **Step 5: Write the failing setup test — `frontend/src/widgets/match-setup/ui/match-setup.test.tsx`**

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { hostFrame } from "../../../../testing/host-frames";
import { renderWithQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { MatchSetup } from "./match-setup";

const MATCH = "33333333-3333-3333-3333-333333333333";

function stock() {
  server.use(
    http.get("/api/library/categories", () =>
      HttpResponse.json([
        {
          id: "c1",
          title: "Кино",
          is_secret: false,
          is_active: true,
          version: 1,
          active_image_count: 9,
        },
        {
          id: "s1",
          title: "Тайна",
          is_secret: true,
          is_active: true,
          version: 1,
          active_image_count: 9,
        },
      ]),
    ),
  );
}

describe("MatchSetup", () => {
  it("shows the stage link the operator has to open on the projector", () => {
    stock();
    renderWithQuery(<MatchSetup frame={hostFrame()} matchId={MATCH} stageToken="tok-123" />);
    expect(screen.getByText(/\/stage\/tok-123/)).toBeInTheDocument();
  });

  it("adds a player with the name that was typed and a colour of its own", async () => {
    const sent: { name: string; colour: string }[] = [];
    stock();
    server.use(
      http.post(`/api/matches/${MATCH}/players`, async ({ request }) => {
        sent.push((await request.json()) as { name: string; colour: string });
        return HttpResponse.json({ outcome: "accepted" });
      }),
    );
    renderWithQuery(
      <MatchSetup frame={hostFrame({ players: [] })} matchId={MATCH} stageToken="t" />,
    );
    await userEvent.type(screen.getByLabelText("Имя"), "Аня");
    await userEvent.click(screen.getByRole("button", { name: "Добавить игрока" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]?.name).toBe("Аня");
    expect(sent[0]?.colour).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it("offers only secret categories as a player's secret", async () => {
    // §2.3: a secret is drawn from the secret pool, not from any theme.
    // Kills on: listing every category — the operator would assign an
    // ordinary theme and the deal would behave in a way nothing explains.
    stock();
    renderWithQuery(<MatchSetup frame={hostFrame()} matchId={MATCH} stageToken="t" />);
    const picker = await screen.findByLabelText("Секрет Аня");
    const options = Array.from(picker.querySelectorAll("option")).map((node) => node.textContent);
    expect(options).toContain("Тайна");
    expect(options).not.toContain("Кино");
  });

  it("labels the button «Раздать» before a board exists", async () => {
    stock();
    renderWithQuery(<MatchSetup frame={hostFrame({ groups: [] })} matchId={MATCH} stageToken="t" />);
    expect(screen.getByRole("button", { name: "Раздать" })).toBeEnabled();
  });

  it("offers a redeal once a board exists, rather than hiding the button", async () => {
    // §3.4. Kills on: disabling or removing it after the first deal — the
    // operator's whole recovery from a bad board is pressing it again.
    stock();
    renderWithQuery(<MatchSetup frame={hostFrame()} matchId={MATCH} stageToken="t" />);
    expect(screen.getByRole("button", { name: "Перераздать" })).toBeEnabled();
  });

  it("shows the reason a deal was refused, and stays usable", async () => {
    // §8: a content gap is an administrator's problem, not a crash.
    stock();
    server.use(
      http.post(`/api/matches/${MATCH}/deal`, () =>
        HttpResponse.json({ outcome: "rejected", reason: "content_unavailable" }, { status: 409 }),
      ),
    );
    renderWithQuery(<MatchSetup frame={hostFrame()} matchId={MATCH} stageToken="t" />);
    await userEvent.click(screen.getByRole("button", { name: "Перераздать" }));
    expect(await screen.findByText(/content_unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Перераздать" })).toBeEnabled();
  });
});
```

- [x] **Step 6: Run it, watch it fail, then write `frontend/src/widgets/match-setup/ui/match-setup.tsx`**

```tsx
import { useState } from "react";
import { useCategories } from "@/features/library";
import { useAddPlayer, useAssignSecret, useDeal, useStart } from "@/features/match-assembly";
import type { HostFrame, OutcomeBody } from "@/shared/api";

// Wide enough for the six players §2.2 allows, and chosen for separation
// on a projector rather than on a monitor.
const COLOURS = ["#e4572e", "#2e86e4", "#3fb950", "#d4a017", "#a371f7", "#e45ea0"];

export interface MatchSetupProps {
  frame: HostFrame;
  matchId: string;
  stageToken: string;
}

export function MatchSetup({ frame, matchId, stageToken }: MatchSetupProps) {
  const categories = useCategories();
  const addPlayer = useAddPlayer();
  const assignSecret = useAssignSecret();
  const deal = useDeal();
  const start = useStart();

  const [name, setName] = useState("");
  const [refusal, setRefusal] = useState<OutcomeBody | null>(null);

  const secrets = categories.data?.filter((row) => row.is_secret && row.is_active) ?? [];

  function note(result: OutcomeBody) {
    setRefusal(result.outcome === "accepted" ? null : result);
  }

  return (
    <section className="flex flex-col gap-8 p-8">
      <p className="rounded-lg bg-white/5 p-3 font-mono text-sm text-stage-muted">
        {`Экран сцены: ${window.location.origin}/stage/${stageToken}`}
      </p>

      <div className="flex items-end gap-3">
        <label className="flex flex-col gap-1 text-sm text-stage-muted">
          Имя
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="rounded-lg bg-white/10 px-3 py-2 text-stage-ink"
          />
        </label>
        <button
          type="button"
          onClick={() => {
            const colour = COLOURS[frame.players.length % COLOURS.length] as string;
            void addPlayer
              .mutateAsync({ matchId, player_id: crypto.randomUUID(), name, colour })
              .then(note);
            setName("");
          }}
          className="rounded-lg bg-white/15 px-4 py-2"
        >
          Добавить игрока
        </button>
      </div>

      <ul className="flex flex-col gap-3">
        {frame.players.map((person) => (
          <li key={person.id} className="flex items-center gap-4">
            <span style={{ color: person.colour }} className="w-32 font-display text-xl">
              {person.name}
            </span>
            <label className="flex items-center gap-2 text-sm text-stage-muted">
              {`Секрет ${person.name}`}
              <select
                aria-label={`Секрет ${person.name}`}
                defaultValue=""
                onChange={(event) =>
                  void assignSecret
                    .mutateAsync({ matchId, player_id: person.id, category: event.target.value })
                    .then(note)
                }
                className="rounded bg-white/10 px-2 py-1 text-stage-ink"
              >
                <option value="" disabled>
                  —
                </option>
                {secrets.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.title}
                  </option>
                ))}
              </select>
            </label>
          </li>
        ))}
      </ul>

      {refusal && (
        <p className="rounded-lg bg-amber-500/15 p-3 text-amber-300">
          {refusal.reason ?? refusal.outcome}
          {refusal.message ? ` — ${refusal.message}` : ""}
        </p>
      )}

      <div className="flex gap-3">
        {/* §3.4: the same route, pressed again, IS «перераздать» — so the
            button never goes away, only its label changes. */}
        <button
          type="button"
          onClick={() => void deal.mutateAsync({ matchId }).then(note)}
          className="rounded-lg bg-white/15 px-5 py-3 font-display text-xl uppercase"
        >
          {frame.groups.length > 0 ? "Перераздать" : "Раздать"}
        </button>
        <button
          type="button"
          onClick={() => void start.mutateAsync({ matchId }).then(note)}
          className="rounded-lg bg-emerald-500/25 px-5 py-3 font-display text-xl uppercase"
        >
          Начать
        </button>
      </div>
    </section>
  );
}
```

`frontend/src/widgets/match-setup/index.ts`: `export { MatchSetup } from "./ui/match-setup";`

- [x] **Step 7: Run it and watch it pass**

Expected: PASS, 6 tests.

- [x] **Step 8: Add match creation to the home screen**

§7.4 puts «сборка партии» on the REST side and in scope. Without this the
console has no way to start a game at all, and the home screen is
permanently empty. Append to `frontend/src/pages/host-home/ui/home-page.test.tsx`:

```tsx
  it("creates a match with the board and player count that were chosen", async () => {
    // §2.1 allows any width and height; §2.2 any player count from two
    // up. Kills on: a hardcoded board — the operator could run one shape
    // of game and no other.
    const made: unknown[] = [];
    server.use(
      http.get("/api/matches", () => HttpResponse.json([])),
      http.post("/api/matches", async ({ request }) => {
        made.push(await request.json());
        return HttpResponse.json(
          { outcome: "accepted", match_id: MATCH.id, stage_token: "tok" },
          { status: 201 },
        );
      }),
    );
    renderWithQuery(<HomePage />);
    await userEvent.clear(await screen.findByLabelText("Ширина"));
    await userEvent.type(screen.getByLabelText("Ширина"), "5");
    await userEvent.clear(screen.getByLabelText("Высота"));
    await userEvent.type(screen.getByLabelText("Высота"), "3");
    await userEvent.clear(screen.getByLabelText("Игроков"));
    await userEvent.type(screen.getByLabelText("Игроков"), "4");
    await userEvent.click(screen.getByRole("button", { name: "Новая партия" }));
    await waitFor(() =>
      expect(made).toEqual([{ board: { width: 5, height: 3 }, player_count: 4 }]),
    );
  });
```

with `import userEvent from "@testing-library/user-event";` and
`import { waitFor } from "@testing-library/react";` added to its imports.

Then, in `frontend/src/pages/host-home/ui/home-page.tsx`, add the form
above the list. The three numbers are local component state — they are
neither server state nor shared, so neither Query nor the store is right
for them.

```tsx
  const create = useCreateMatch();
  const [width, setWidth] = useState(4);
  const [height, setHeight] = useState(3);
  const [players, setPlayers] = useState(3);
```

```tsx
      <div className="flex items-end gap-3 rounded-xl bg-white/5 p-4">
        {(
          [
            ["Ширина", width, setWidth],
            ["Высота", height, setHeight],
            ["Игроков", players, setPlayers],
          ] as const
        ).map(([label, value, set]) => (
          <label key={label} className="flex flex-col gap-1 text-sm text-stage-muted">
            {label}
            <input
              type="number"
              min={1}
              value={value}
              onChange={(event) => set(Number(event.target.value))}
              className="w-20 rounded-lg bg-white/10 px-3 py-2 text-stage-ink"
            />
          </label>
        ))}
        <button
          type="button"
          onClick={() =>
            void create.mutateAsync({
              board: { width, height },
              player_count: players,
            })
          }
          className="rounded-lg bg-white/15 px-4 py-2"
        >
          Новая партия
        </button>
      </div>
```

with `import { useState } from "react";` and
`import { useCreateMatch } from "@/features/match-assembly";` added.

`useState(Number(...))` on an emptied field yields `NaN`; the `min={1}`
attribute does not prevent that, and the server rejects it with an
ordinary outcome rather than a crash — which is the right place for that
check to live (§6.3), not a second validator here.

- [x] **Step 9: Run the home-page tests and watch them pass**

```bash
cd frontend && pnpm vitest run src/pages/host-home; echo "exit=$?"
```

Expected: PASS, 4 tests.

- [x] **Step 10: Green everything and commit**

```bash
cd frontend && pnpm build; echo "exit=$?"
pnpm check; echo "exit=$?"
pnpm test; echo "exit=$?"
cd .. && git add frontend && git commit -m "feat(host): assemble a match — players, secrets, deal and redeal"
```

---

## Task 4: The host socket, and the board maths both surfaces share

The live transport, and H3's move of the board geometry down a layer.

**Files:**
- Move: `frontend/src/widgets/board/lib/geometry.ts` and its test → `frontend/src/entities/board/lib/`
- Create: `frontend/src/entities/board/index.ts`
- Modify: `frontend/src/widgets/board/ui/group-shape.tsx`
- Create: `frontend/src/shared/api/host-socket.ts`, `.../host-socket.test.ts`; Modify: `frontend/src/shared/api/index.ts`
- Modify: `frontend/testing/fake-socket.ts`
- Create: `frontend/src/features/host-commands/api/use-host-match.ts`, `.../api/use-host-match.test.tsx`, `.../model/selection.ts`, `.../index.ts`

**Interfaces:**
- Produces:
  - `outlinePath`, `labelAnchor` from `@/entities/board`
  - `connectHost({ url, onFrame, onAck, onStatus, factory }): { send(command): string; dispose(): void }`
  - `useHostMatch(matchId, factory?): { frame, status, connected, refusal, send }`
  - `useSelection()` — zustand: `{ selected, select, clear }`

- [x] **Step 1: Move the geometry**

```bash
cd frontend && mkdir -p src/entities/board/lib
git mv src/widgets/board/lib/geometry.ts src/entities/board/lib/geometry.ts
git mv src/widgets/board/lib/geometry.test.ts src/entities/board/lib/geometry.test.ts
rmdir src/widgets/board/lib
```

`frontend/src/entities/board/index.ts`:

```ts
export { labelAnchor, outlinePath } from "./lib/geometry";
```

Then in `src/widgets/board/ui/group-shape.tsx` change the import from `"../lib/geometry"` to `"@/entities/board"`, and fix the moved test's relative import of `testing/frames` — it gains one `../` level.

- [x] **Step 2: Confirm the move changed no behaviour**

```bash
cd frontend && pnpm test; echo "exit=$?"
pnpm check; echo "exit=$?"
```

Expected: the same test count as before the move, all passing. `steiger.config.ts` already exempts `./src/entities/**` from `fsd/insignificant-slice`, so the new slice will not fire it.

- [x] **Step 3: Commit the move on its own**

A pure move, committed separately, so the next reviewer can see at a glance that nothing in it changed.

```bash
cd .. && git add -A frontend && git commit -m "refactor(board): move board geometry to entities so both boards can share it"
```

- [ ] **Step 4: Extend `frontend/testing/fake-socket.ts`**

`FakeSocket` has to record what was written. Add `send(data: string): void;` to the `SocketLike` interface, and to the class:

```ts
  readonly sent: string[] = [];

  send(data: string): void {
    this.sent.push(data);
  }
```

- [ ] **Step 5: Write the failing host-socket test — `frontend/src/shared/api/host-socket.test.ts`**

```ts
import { describe, expect, it, vi } from "vitest";
import { fakeSocketFactory } from "../../../testing/fake-socket";
import { hostFrame } from "../../../testing/host-frames";
import { connectHost } from "./host-socket";

describe("connectHost", () => {
  it("routes frames and acks down separate channels", () => {
    // One socket carries two message kinds (§7.6). Kills on: routing by
    // arrival order rather than by the `kind` discriminator — an ack
    // rendered as a frame blanks the console mid-match.
    const { factory, sockets } = fakeSocketFactory();
    const onFrame = vi.fn();
    const onAck = vi.fn();
    connectHost({ url: "ws://x", onFrame, onAck, factory });

    sockets[0]?.open();
    sockets[0]?.deliver({ kind: "ack", correlation_id: "c1", outcome: "accepted" });
    sockets[0]?.deliver(hostFrame({ seq: 9 }));

    expect(onAck).toHaveBeenCalledTimes(1);
    expect(onFrame).toHaveBeenCalledTimes(1);
    expect(onFrame.mock.calls[0]?.[0].seq).toBe(9);
  });

  it("wraps a command in an envelope carrying a correlation id", () => {
    // §7.6: the correlation id is how an operator's retry is matched to
    // its answer. Kills on: sending the bare command.
    const { factory, sockets } = fakeSocketFactory();
    const channel = connectHost({ url: "ws://x", onFrame: vi.fn(), onAck: vi.fn(), factory });
    sockets[0]?.open();
    const id = channel.send({ type: "judge_correct" });

    const sent = JSON.parse(sockets[0]?.sent[0] ?? "{}");
    expect(sent.command).toEqual({ type: "judge_correct" });
    expect(sent.correlation_id).toBe(id);
    expect(id).toBeTruthy();
  });

  it("does not reconnect after an authentication refusal", () => {
    // 1008 means the cookie is gone. Retrying cannot succeed; it would
    // hammer the server forever while the operator stares at a console
    // that never says why it is empty.
    vi.useFakeTimers();
    const { factory, sockets } = fakeSocketFactory();
    const onStatus = vi.fn();
    connectHost({ url: "ws://x", onFrame: vi.fn(), onAck: vi.fn(), onStatus, factory });
    sockets[0]?.open();
    sockets[0]?.serverClose(1008);
    vi.advanceTimersByTime(60_000);

    expect(sockets).toHaveLength(1);
    expect(onStatus).toHaveBeenLastCalledWith("refused");
    vi.useRealTimers();
  });

  it("reconnects after an ordinary drop", () => {
    vi.useFakeTimers();
    const { factory, sockets } = fakeSocketFactory();
    connectHost({ url: "ws://x", onFrame: vi.fn(), onAck: vi.fn(), factory });
    sockets[0]?.open();
    sockets[0]?.serverClose(1006);
    vi.advanceTimersByTime(500);

    expect(sockets).toHaveLength(2);
    vi.useRealTimers();
  });

  it("stops everything on dispose", () => {
    vi.useFakeTimers();
    const { factory, sockets } = fakeSocketFactory();
    const channel = connectHost({ url: "ws://x", onFrame: vi.fn(), onAck: vi.fn(), factory });
    sockets[0]?.open();
    channel.dispose();
    sockets[0]?.serverClose(1006);
    vi.advanceTimersByTime(60_000);

    expect(sockets).toHaveLength(1);
    expect(sockets[0]?.closed).toBe(true);
    vi.useRealTimers();
  });
});
```

- [ ] **Step 6: Run it, watch it fail, then write `frontend/src/shared/api/host-socket.ts`**

```ts
import type { Ack, Envelope, HostFrame } from "./contracts";

export type HostCommand = Envelope["command"];
export type HostStatus = "connected" | "dropped" | "refused";

export interface HostSocketLike {
  close(): void;
  send(data: string): void;
  onopen: ((this: unknown, ev: unknown) => void) | null;
  onmessage: ((this: unknown, ev: { data: string }) => void) | null;
  onclose: ((this: unknown, ev: { code: number }) => void) | null;
  onerror: ((this: unknown, ev: unknown) => void) | null;
}

export type HostSocketFactory = (url: string) => HostSocketLike;

export interface HostConnection {
  url: string;
  onFrame: (frame: HostFrame) => void;
  onAck: (ack: Ack) => void;
  onStatus?: ((status: HostStatus) => void) | undefined;
  factory?: HostSocketFactory | undefined;
}

export interface HostChannel {
  send: (command: HostCommand) => string;
  dispose: () => void;
}

const FIRST_RETRY_MS = 500;
const MAX_RETRY_MS = 8000;
/** 1008 is "policy violation", which the server uses for "not
 * authenticated". Retrying it cannot succeed. */
const UNAUTHORISED = 1008;

export function connectHost({
  url,
  onFrame,
  onAck,
  onStatus,
  factory,
}: HostConnection): HostChannel {
  const open = factory ?? ((target: string) => new WebSocket(target) as unknown as HostSocketLike);
  let disposed = false;
  let socket: HostSocketLike | null = null;
  let retry: ReturnType<typeof setTimeout> | null = null;
  let backoff = FIRST_RETRY_MS;

  function connect(): void {
    if (disposed) return;
    const current = open(url);
    socket = current;

    current.onopen = () => {
      if (disposed) return;
      backoff = FIRST_RETRY_MS;
      onStatus?.("connected");
    };

    current.onmessage = (event) => {
      if (disposed) return;
      let payload: unknown;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      if (typeof payload !== "object" || payload === null) return;
      // Routed by the discriminator, never by arrival order: one channel
      // carries both, and a frame may land between a command and its ack.
      const kind = (payload as { kind?: unknown }).kind;
      if (kind === "host") onFrame(payload as HostFrame);
      else if (kind === "ack") onAck(payload as Ack);
    };

    current.onclose = (event) => {
      if (disposed) return;
      if (event.code === UNAUTHORISED) {
        onStatus?.("refused");
        return;
      }
      onStatus?.("dropped");
      retry = setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, MAX_RETRY_MS);
    };

    current.onerror = () => current.close();
  }

  connect();

  return {
    send: (command: HostCommand) => {
      const correlationId = crypto.randomUUID();
      socket?.send(JSON.stringify({ correlation_id: correlationId, command }));
      return correlationId;
    },
    dispose: () => {
      disposed = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    },
  };
}
```

Add these exports to `frontend/src/shared/api/index.ts`:

```ts
export {
  connectHost,
  type HostChannel,
  type HostCommand,
  type HostConnection,
  type HostSocketFactory,
  type HostSocketLike,
  type HostStatus,
} from "./host-socket";
```

- [ ] **Step 7: Run it and watch it pass**

Expected: PASS, 5 tests.

- [ ] **Step 8: Write `frontend/src/features/host-commands/model/selection.ts`**

H7: one field, and Task 7 checks it stayed that way.

```ts
import { create } from "zustand";

interface Selection {
  selected: string | null;
  select: (groupId: string) => void;
  clear: () => void;
}

/** The group the operator has picked, between duels. Client-only and
 * transient: it belongs to no frame, and it is read by two components that
 * are not parent and child. Nothing else belongs in this store — match
 * state has exactly one source, and it is the socket (H1). */
export const useSelection = create<Selection>((set) => ({
  selected: null,
  select: (groupId) => set({ selected: groupId }),
  clear: () => set({ selected: null }),
}));
```

- [ ] **Step 9: Write the failing hook test — `frontend/src/features/host-commands/api/use-host-match.test.tsx`**

```tsx
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fakeSocketFactory } from "../../../../testing/fake-socket";
import { hostFrame } from "../../../../testing/host-frames";
import { useHostMatch } from "./use-host-match";

describe("useHostMatch", () => {
  it("holds the last frame", () => {
    const { factory, sockets } = fakeSocketFactory();
    const { result } = renderHook(() => useHostMatch("m1", factory));
    act(() => {
      sockets[0]?.open();
      sockets[0]?.deliver(hostFrame({ seq: 4 }));
      sockets[0]?.deliver(hostFrame({ seq: 11 }));
    });
    expect(result.current.frame?.seq).toBe(11);
  });

  it("surfaces a rejection so the operator can read it", () => {
    // §6.3: a rejection is an ordinary outcome and it has a reason. Kills
    // on: swallowing the ack — the operator presses a button, nothing
    // happens, and nothing says why.
    const { factory, sockets } = fakeSocketFactory();
    const { result } = renderHook(() => useHostMatch("m1", factory));
    act(() => {
      sockets[0]?.open();
      sockets[0]?.deliver({
        kind: "ack",
        correlation_id: "c1",
        outcome: "rejected",
        reason: "not_adjacent",
      });
    });
    expect(result.current.refusal?.reason).toBe("not_adjacent");
  });

  it("clears a refusal once a command is accepted", () => {
    // Kills on: a sticky banner that outlives the problem, which the
    // operator learns to ignore.
    const { factory, sockets } = fakeSocketFactory();
    const { result } = renderHook(() => useHostMatch("m1", factory));
    act(() => {
      sockets[0]?.open();
      sockets[0]?.deliver({ kind: "ack", correlation_id: "c1", outcome: "rejected", reason: "x" });
    });
    expect(result.current.refusal).not.toBeNull();
    act(() => {
      sockets[0]?.deliver({ kind: "ack", correlation_id: "c2", outcome: "accepted" });
    });
    expect(result.current.refusal).toBeNull();
  });

  it("sends a command down the socket", () => {
    const { factory, sockets } = fakeSocketFactory();
    const { result } = renderHook(() => useHostMatch("m1", factory));
    act(() => {
      sockets[0]?.open();
      result.current.send({ type: "pause_duel" });
    });
    expect(JSON.parse(sockets[0]?.sent[0] ?? "{}").command).toEqual({ type: "pause_duel" });
  });

  it("does not touch the frame when a command is sent", () => {
    // H1, the ruling this whole surface rests on. Kills on: patching
    // local state on send — the console would show a judgement the server
    // may still reject.
    const { factory, sockets } = fakeSocketFactory();
    const { result } = renderHook(() => useHostMatch("m1", factory));
    act(() => {
      sockets[0]?.open();
      sockets[0]?.deliver(hostFrame({ seq: 4, round_no: 2 }));
      result.current.send({ type: "judge_correct" });
    });
    expect(result.current.frame?.seq).toBe(4);
    expect(result.current.frame?.round_no).toBe(2);
  });
});
```

- [ ] **Step 10: Run it, watch it fail, then write `frontend/src/features/host-commands/api/use-host-match.ts`**

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import {
  type Ack,
  type HostChannel,
  type HostCommand,
  type HostFrame,
  type HostSocketFactory,
  type HostStatus,
  connectHost,
} from "@/shared/api";

export interface HostMatch {
  frame: HostFrame | null;
  status: HostStatus | "connecting";
  connected: boolean;
  refusal: Ack | null;
  send: (command: HostCommand) => void;
}

/** The console's live view of one match.
 *
 * H1: nothing here writes `frame` except a frame arriving off the socket.
 * A command goes out and the answer comes back as the next frame; the ack
 * exists only so a refusal can be put in front of the operator.
 */
export function useHostMatch(matchId: string, factory?: HostSocketFactory): HostMatch {
  const [frame, setFrame] = useState<HostFrame | null>(null);
  const [status, setStatus] = useState<HostStatus | "connecting">("connecting");
  const [refusal, setRefusal] = useState<Ack | null>(null);
  const channel = useRef<HostChannel | null>(null);

  useEffect(() => {
    setFrame(null);
    setRefusal(null);
    setStatus("connecting");
    const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    const connection = connectHost({
      url: `${scheme}//${window.location.host}/ws/host/${matchId}`,
      factory,
      onFrame: setFrame,
      onAck: (ack) => setRefusal(ack.outcome === "accepted" || ack.outcome === "noop" ? null : ack),
      onStatus: setStatus,
    });
    channel.current = connection;
    return () => {
      channel.current = null;
      connection.dispose();
    };
    // `factory` is a test seam handed in once; re-subscribing on its
    // identity would reconnect on every render.
    // biome-ignore lint/correctness/useExhaustiveDependencies: see above
  }, [matchId]);

  const send = useCallback((command: HostCommand) => {
    channel.current?.send(command);
  }, []);

  return { frame, status, connected: status === "connected", refusal, send };
}
```

`frontend/src/features/host-commands/index.ts`:

```ts
export { type HostMatch, useHostMatch } from "./api/use-host-match";
export { useSelection } from "./model/selection";
```

If Biome objects to the `biome-ignore` comment's placement, put it on the line immediately before the `useEffect(` call — Biome anchors a suppression to the line before the diagnostic's start, which is the call, not the dependency array.

- [ ] **Step 11: Green everything and commit**

```bash
cd frontend && pnpm build; echo "exit=$?"
pnpm check; echo "exit=$?"
pnpm test; echo "exit=$?"
cd .. && git add frontend && git commit -m "feat(host): the command socket, and one refusal the operator can read"
```

---

## Task 5: Between duels — selection, legal targets, declaration

§9.2's middle screen, and H2's central claim: «Правило смежности не проверяется, а делается невозможным».

**Files:**
- Create: `frontend/src/widgets/host-board/ui/host-board.tsx`, `.../ui/host-board.test.tsx`, `.../index.ts`

**Interfaces:**
- Produces: `<HostBoard frame onDeclare={(attacking, defending) => void} />`

- [ ] **Step 1: Write the failing test — `frontend/src/widgets/host-board/ui/host-board.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSelection } from "@/features/host-commands";
import { ATTACKER, DEFENDER, hostFrame, hostGroup } from "../../../../testing/host-frames";
import { HostBoard } from "./host-board";

const FRAME = hostFrame({
  current_player: ATTACKER,
  groups: [
    hostGroup({ id: "g1", owner: ATTACKER, cells: [{ col: 0, row: 0 }] }),
    hostGroup({
      id: "g2",
      owner: DEFENDER,
      cells: [{ col: 1, row: 0 }],
      category: { id: "c2", name: "Спорт" },
    }),
    hostGroup({
      id: "g3",
      owner: DEFENDER,
      cells: [{ col: 2, row: 2 }],
      category: { id: "c3", name: "Еда" },
    }),
  ],
  legal_attacks: { g1: ["g2"] },
});

describe("HostBoard", () => {
  beforeEach(() => {
    useSelection.getState().clear();
  });

  it("names every category, including another player's secret", () => {
    // §9.2: «Поле целиком со всеми категориями, включая чужие секреты» —
    // the exact opposite of the stage screen, and the reason the two
    // frames are different types.
    render(<HostBoard frame={FRAME} onDeclare={vi.fn()} />);
    expect(screen.getByText("Спорт")).toBeInTheDocument();
    expect(screen.getByText("Еда")).toBeInTheDocument();
    expect(screen.queryByText("Секрет")).toBeNull();
  });

  it("offers only groups that can attack as a first click", () => {
    // H2: `legal_attacks` has one key, so every other group is inert.
    render(<HostBoard frame={FRAME} onDeclare={vi.fn()} />);
    expect(screen.getByTestId("group-g1")).toBeEnabled();
    expect(screen.getByTestId("group-g2")).toBeDisabled();
    expect(screen.getByTestId("group-g3")).toBeDisabled();
  });

  it("dims illegal targets once an attacker is picked", async () => {
    // §9.2: «нелегальные цели гаснут, ярко только ортогонально смежные
    // чужие». Kills on: leaving every group bright and checking on click —
    // the operator would aim at a target that then refuses.
    render(<HostBoard frame={FRAME} onDeclare={vi.fn()} />);
    await userEvent.click(screen.getByTestId("group-g1"));
    expect(screen.getByTestId("group-g2")).toHaveAttribute("data-legal", "true");
    expect(screen.getByTestId("group-g3")).toHaveAttribute("data-legal", "false");
    expect(screen.getByTestId("group-g3")).toBeDisabled();
  });

  it("declares the attack on the second click", async () => {
    const onDeclare = vi.fn();
    render(<HostBoard frame={FRAME} onDeclare={onDeclare} />);
    await userEvent.click(screen.getByTestId("group-g1"));
    await userEvent.click(screen.getByTestId("group-g2"));
    expect(onDeclare).toHaveBeenCalledWith("g1", "g2");
  });

  it("cannot declare an illegal attack even when the click is forced", async () => {
    // H2 from the other side: this is the property, not the dimming.
    // Kills on: rendering an illegal target as a live button.
    const onDeclare = vi.fn();
    render(<HostBoard frame={FRAME} onDeclare={onDeclare} />);
    await userEvent.click(screen.getByTestId("group-g1"));
    await userEvent.click(screen.getByTestId("group-g3"));
    expect(onDeclare).not.toHaveBeenCalled();
  });

  it("lets the operator change their mind by re-picking the attacker", async () => {
    const onDeclare = vi.fn();
    render(<HostBoard frame={FRAME} onDeclare={onDeclare} />);
    await userEvent.click(screen.getByTestId("group-g1"));
    await userEvent.click(screen.getByTestId("group-g1"));
    expect(onDeclare).not.toHaveBeenCalled();
    expect(screen.getByTestId("group-g1")).toHaveAttribute("data-selected", "false");
  });

  it("offers nothing at all when no attack is legal", () => {
    // A stuck console is the intended failure (H2's cost note), and it
    // must not be a crash.
    render(<HostBoard frame={hostFrame({ legal_attacks: {} })} onDeclare={vi.fn()} />);
    expect(screen.getByTestId("group-g1")).toBeDisabled();
  });

  it("draws one outline per group, not one per cell", () => {
    // §9.1's rule applies to this board too: a group of N cells has to
    // read as one object.
    const frame = hostFrame({
      board: { width: 3, height: 1 },
      groups: [
        hostGroup({
          id: "g1",
          cells: [
            { col: 0, row: 0 },
            { col: 1, row: 0 },
          ],
        }),
      ],
      legal_attacks: {},
    });
    const { container } = render(<HostBoard frame={frame} onDeclare={vi.fn()} />);
    expect(container.querySelectorAll("[data-outline]")).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd frontend && pnpm vitest run src/widgets/host-board; echo "exit=$?"
```

Expected: FAIL — `Failed to resolve import "./host-board"`.

- [ ] **Step 3: Write `frontend/src/widgets/host-board/ui/host-board.tsx`**

Unlike the stage board, this one is an **input surface**: every group has to be a real, focusable, disable-able control. So the groups are HTML `<button>`s laid out on a CSS grid, with the true group outlines drawn in a non-interactive SVG overlay above them — which is how a multi-cell group still reads as one object while each click target stays a button.

```tsx
import { labelAnchor, outlinePath } from "@/entities/board";
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
  const selected = useSelection((state) => state.selected);
  const select = useSelection((state) => state.select);
  const clear = useSelection((state) => state.clear);

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
```

`labelAnchor` is imported but unused here — the label lives in the button, not the SVG. Drop it from the import rather than leaving it: `noUnusedLocals` is on.

`frontend/src/widgets/host-board/index.ts`: `export { HostBoard } from "./ui/host-board";`

- [ ] **Step 4: Run it and watch it pass**

Expected: PASS, 8 tests.

- [ ] **Step 5: Green everything and commit**

```bash
cd frontend && pnpm build; echo "exit=$?"
pnpm check; echo "exit=$?"
pnpm test; echo "exit=$?"
cd .. && git add frontend && git commit -m "feat(host): make an illegal attack unreachable rather than refused"
```

---

## Task 6: The judging screen, and the console page

§9.2's «Судейство» — the layout chosen for one decision every five seconds — plus the page that picks between setup, board and judging.

**Files:**
- Create: `frontend/src/shared/lib/use-hotkeys.ts`, `.../use-hotkeys.test.tsx`
- Create: `frontend/src/entities/match/model/host-beat.ts`, `.../host-beat.test.ts`; Modify: `frontend/src/entities/match/index.ts`
- Create: `frontend/src/widgets/judging/ui/judging-panel.tsx`, `.../ui/judging-panel.test.tsx`, `.../index.ts`
- Create: `frontend/src/pages/host-match/ui/match-page.tsx`, `.../ui/match-page.test.tsx`, `.../index.ts`
- Modify: `frontend/src/app/routes/host.match.$matchId.tsx`

**Interfaces:**
- Produces:
  - `useHotkeys(map: Record<string, () => void>, active: boolean)` — keys are `KeyboardEvent.code` values, optionally prefixed `Ctrl+`
  - `hostBeatOf(frame): "setup" | "board" | "judging" | "over"`
  - `<JudgingPanel frame duel now send />`, `<MatchPage matchId socketFactory? />`

- [ ] **Step 1: Write the failing hotkeys test — `frontend/src/shared/lib/use-hotkeys.test.tsx`**

```tsx
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useHotkeys } from "./use-hotkeys";

describe("useHotkeys", () => {
  it("fires on the physical key, whatever the layout reports", () => {
    // H4: on a Russian layout the physical P key reports `key === "з"`.
    // Kills on: matching `event.key` — the operator running a Russian
    // show loses «пас», and every US-layout test still passes.
    const pass = vi.fn();
    renderHook(() => useHotkeys({ KeyP: pass }, true));
    document.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyP", key: "з", bubbles: true }));
    expect(pass).toHaveBeenCalledTimes(1);
  });

  it("does not fire while a text field has focus", () => {
    // H5. Kills on: a global handler that judges the duel when the
    // operator types a space into an answer field.
    const correct = vi.fn();
    const input = document.createElement("input");
    document.body.append(input);
    input.focus();
    renderHook(() => useHotkeys({ Space: correct }, true));
    document.dispatchEvent(new KeyboardEvent("keydown", { code: "Space", bubbles: true }));
    expect(correct).not.toHaveBeenCalled();
    input.remove();
  });

  it("distinguishes a modified key from a bare one", () => {
    const undo = vi.fn();
    const bare = vi.fn();
    renderHook(() => useHotkeys({ "Ctrl+KeyZ": undo, KeyZ: bare }, true));
    document.dispatchEvent(
      new KeyboardEvent("keydown", { code: "KeyZ", ctrlKey: true, bubbles: true }),
    );
    expect(undo).toHaveBeenCalledTimes(1);
    expect(bare).not.toHaveBeenCalled();
  });

  it("does nothing while inactive", () => {
    const correct = vi.fn();
    renderHook(() => useHotkeys({ Space: correct }, false));
    document.dispatchEvent(new KeyboardEvent("keydown", { code: "Space", bubbles: true }));
    expect(correct).not.toHaveBeenCalled();
  });

  it("stops listening on unmount", () => {
    const correct = vi.fn();
    const { unmount } = renderHook(() => useHotkeys({ Space: correct }, true));
    unmount();
    document.dispatchEvent(new KeyboardEvent("keydown", { code: "Space", bubbles: true }));
    expect(correct).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run it, watch it fail, then write `frontend/src/shared/lib/use-hotkeys.ts`**

```ts
import { useEffect, useRef } from "react";

const TYPING = new Set(["INPUT", "TEXTAREA", "SELECT"]);

function isTyping(): boolean {
  const active = document.activeElement;
  if (!active) return false;
  if (TYPING.has(active.tagName)) return true;
  return active instanceof HTMLElement && active.isContentEditable;
}

/** Window-level keyboard handling for §9.2's judging tempo.
 *
 * Keys are `KeyboardEvent.code` values — the *physical* key — optionally
 * prefixed `Ctrl+`. H4: `event.key` reports the character the current
 * layout produces, and the operator running a Russian-language show has a
 * Russian layout, where P is «з».
 */
export function useHotkeys(map: Record<string, () => void>, active: boolean): void {
  // Callers rebuild the map every render; a ref keeps the listener stable
  // so it is not torn down and rebound on every frame.
  const latest = useRef(map);
  latest.current = map;

  useEffect(() => {
    if (!active) return;
    function onKeyDown(event: KeyboardEvent): void {
      if (isTyping()) return;
      const combo = `${event.ctrlKey || event.metaKey ? "Ctrl+" : ""}${event.code}`;
      const handler = latest.current[combo];
      if (!handler) return;
      // Space scrolls the page and Ctrl+Z reaches the browser's own undo.
      event.preventDefault();
      handler();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [active]);
}
```

- [ ] **Step 3: Run it and watch it pass**

Expected: PASS, 5 tests.

- [ ] **Step 4: Write the failing beat test — `frontend/src/entities/match/model/host-beat.test.ts`**

```ts
import { describe, expect, it } from "vitest";
import { hostDuel, hostFrame } from "../../../../testing/host-frames";
import { hostBeatOf } from "./host-beat";

describe("hostBeatOf", () => {
  it("is setup before the match starts", () => {
    expect(hostBeatOf(hostFrame({ status: "setup" }))).toBe("setup");
  });

  it("is the board between duels", () => {
    expect(hostBeatOf(hostFrame({ status: "running", duel: null }))).toBe("board");
  });

  it("is judging as soon as an attack is declared", () => {
    // The presenter explains the category during «declared», and the
    // answer and the buttons must already be in front of the operator
    // when the duel starts. Kills on: waiting for `phase === "running"`.
    expect(hostBeatOf(hostFrame({ duel: hostDuel({ phase: "declared" }) }))).toBe("judging");
  });

  it("is judging while the duel runs", () => {
    expect(hostBeatOf(hostFrame({ duel: hostDuel({ phase: "running" }) }))).toBe("judging");
  });

  it("is over once the match finishes, whatever else the frame carries", () => {
    // Kills on: letting a stale duel outrank a finished match, which
    // would leave the operator judging a game that has ended.
    expect(hostBeatOf(hostFrame({ status: "finished", duel: hostDuel() }))).toBe("over");
  });

  it("is total over every reachable frame", () => {
    for (const frame of [
      hostFrame({ status: "setup", groups: [] }),
      hostFrame({ status: "running", groups: [] }),
      hostFrame({ status: "finished", winner: null }),
    ]) {
      expect(hostBeatOf(frame)).toBeTruthy();
    }
  });
});
```

- [ ] **Step 5: Run it, watch it fail, then write `frontend/src/entities/match/model/host-beat.ts`**

```ts
import type { HostFrame } from "@/shared/api";

export type HostBeat = "setup" | "board" | "judging" | "over";

/** Which of §9.2's screens a frame wants. Pure and total, for the same
 * reason `beatOf` is on the stage side: the console renders what the frame
 * says and decides nothing itself. */
export function hostBeatOf(frame: HostFrame): HostBeat {
  if (frame.status === "finished") return "over";
  if (frame.status === "setup") return "setup";
  return frame.duel === null ? "board" : "judging";
}
```

Add to `frontend/src/entities/match/index.ts`:

```ts
export { type HostBeat, hostBeatOf } from "./model/host-beat";
```

- [ ] **Step 6: Run it and watch it pass**

Expected: PASS, 6 tests.

- [ ] **Step 7: Write the failing judging test — `frontend/src/widgets/judging/ui/judging-panel.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ATTACKER, hostDuel, hostFrame, timing } from "../../../../testing/host-frames";
import { JudgingPanel } from "./judging-panel";

const anchor = Date.parse("2026-08-23T20:00:00Z");

function panel(duel = hostDuel({ phase: "running" })) {
  const send = vi.fn();
  render(<JudgingPanel frame={hostFrame({ duel })} duel={duel} now={() => anchor} send={send} />);
  return send;
}

describe("JudgingPanel", () => {
  it("puts the correct answer in the centre", () => {
    // §9.2: «правильный ответ гигантским кеглем в центре». This is the
    // one thing the operator's eye must find without searching.
    panel();
    expect(screen.getByTestId("answer")).toHaveTextContent("Титаник");
  });

  it("counts the pictures so the operator sees the category running out", () => {
    // §9.2: «Счётчик картинок нужен не игре, а ведущему».
    panel(hostDuel({ phase: "running", index: 1, image_count: 4 }));
    expect(screen.getByTestId("picture-count")).toHaveTextContent("2 / 4");
  });

  it("shows a thumbnail of the picture the room is looking at", () => {
    panel(hostDuel({ phase: "running", index: 1 }));
    expect(screen.getByAltText("Титаник")).toHaveAttribute("src", `/api/media/${"b".repeat(64)}`);
  });

  it("offers the three judgements, with undo visually quieter", () => {
    // §9.2: «три кнопки внизу. Отмена рядом, но визуально тише».
    panel();
    expect(screen.getByRole("button", { name: "Верно" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Пас" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Пауза" })).toBeInTheDocument();
    expect(screen.getByTestId("undo")).toHaveAttribute("data-quiet", "true");
  });

  it("sends the right command for each button", async () => {
    const send = panel();
    await userEvent.click(screen.getByRole("button", { name: "Верно" }));
    await userEvent.click(screen.getByRole("button", { name: "Пас" }));
    await userEvent.click(screen.getByRole("button", { name: "Пауза" }));
    await userEvent.click(screen.getByTestId("undo"));
    expect(send.mock.calls.map(([command]) => command.type)).toEqual([
      "judge_correct",
      "judge_pass",
      "pause_duel",
      "undo_last_judgement",
    ]);
  });

  it("judges on the space bar and passes on the physical P", () => {
    // §9.2: «Мышью такой темп не выдерживается». H4 for the layout.
    const send = panel();
    document.dispatchEvent(new KeyboardEvent("keydown", { code: "Space", bubbles: true }));
    document.dispatchEvent(new KeyboardEvent("keydown", { code: "KeyP", key: "з", bubbles: true }));
    document.dispatchEvent(new KeyboardEvent("keydown", { code: "Escape", bubbles: true }));
    document.dispatchEvent(
      new KeyboardEvent("keydown", { code: "KeyZ", ctrlKey: true, bubbles: true }),
    );
    expect(send.mock.calls.map(([command]) => command.type)).toEqual([
      "judge_correct",
      "judge_pass",
      "pause_duel",
      "undo_last_judgement",
    ]);
  });

  it("resumes rather than pauses when the duel is already paused", () => {
    // Kills on: sending `pause_duel` twice — the operator's escape key
    // would be a dead key for the whole pause.
    const send = panel(hostDuel({ phase: "running", timing: timing({ paused: true }) }));
    document.dispatchEvent(new KeyboardEvent("keydown", { code: "Escape", bubbles: true }));
    expect(send).toHaveBeenCalledWith({ type: "resume_duel" });
  });

  it("starts the duel rather than judging it while the attack is only declared", () => {
    // Beat 1 on the stage screen is the presenter explaining the
    // category; the console's button there is «Начать дуэль».
    const send = panel(hostDuel({ phase: "declared" }));
    expect(screen.getByRole("button", { name: "Начать дуэль" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Верно" })).toBeNull();
    document.dispatchEvent(new KeyboardEvent("keydown", { code: "Space", bubbles: true }));
    expect(send).toHaveBeenCalledWith({ type: "start_duel" });
  });

  it("shows both clocks", () => {
    panel();
    expect(screen.getAllByRole("timer")).toHaveLength(2);
    expect(screen.getByTestId(`clock-${ATTACKER}`)).toHaveTextContent("1:00");
  });
});
```

- [ ] **Step 8: Run it, watch it fail, then write `frontend/src/widgets/judging/ui/judging-panel.tsx`**

```tsx
import { remainingAt } from "@/entities/duel";
import { colourOf } from "@/entities/match";
import type { HostCommand, HostDuelFrame, HostFrame } from "@/shared/api";
import { mediaUrl } from "@/shared/config";
import { useHotkeys } from "@/shared/lib/use-hotkeys";
import { formatClock } from "@/widgets/duel";

export interface JudgingPanelProps {
  frame: HostFrame;
  duel: HostDuelFrame;
  now: () => number;
  send: (command: HostCommand) => void;
}

/** §9.2's judging layout: two clocks on top, the answer enormous in the
 * middle, a thumbnail and a counter beside it, three buttons underneath.
 * There is deliberately no answer queue and no board here — §9.2 says both
 * add eye movement exactly where its cost is highest. */
export function JudgingPanel({ frame, duel, now, send }: JudgingPanelProps) {
  const running = duel.phase === "running";
  const paused = duel.timing.paused;
  const at = now();

  const pauseOrResume = () => send({ type: paused ? "resume_duel" : "pause_duel" });

  useHotkeys(
    running
      ? {
          Space: () => send({ type: "judge_correct" }),
          KeyP: () => send({ type: "judge_pass" }),
          Escape: pauseOrResume,
          "Ctrl+KeyZ": () => send({ type: "undo_last_judgement" }),
        }
      : { Space: () => send({ type: "start_duel" }) },
    true,
  );

  const digest = duel.image_order[duel.index];

  return (
    <section className="flex h-full flex-col gap-6 p-8">
      <div className="flex gap-6">
        {[duel.attacker, duel.defender].map((playerId) => {
          const colour = colourOf(frame, playerId);
          const active = duel.timing.answering === playerId && !paused;
          return (
            <div
              key={playerId}
              role="timer"
              data-testid={`clock-${playerId}`}
              data-active={active}
              style={active ? { background: colour } : undefined}
              className="flex flex-1 flex-col items-center rounded-xl py-3 data-[active=false]:opacity-45"
            >
              <span>{frame.players.find((person) => person.id === playerId)?.name ?? "—"}</span>
              <span className="font-display text-6xl tabular-nums">
                {formatClock(remainingAt(duel.timing, playerId, at))}
              </span>
            </div>
          );
        })}
      </div>

      <div className="flex min-h-0 flex-1 items-center gap-8">
        <div className="flex w-56 flex-col gap-2">
          {digest !== undefined && (
            <img
              src={mediaUrl(digest)}
              alt={duel.current_answer ?? ""}
              className="w-full rounded-lg object-contain"
            />
          )}
          <span data-testid="picture-count" className="text-center text-stage-muted">
            {`${duel.index + 1} / ${duel.image_count}`}
          </span>
        </div>
        <p
          data-testid="answer"
          className="flex-1 text-center font-display text-[8rem] uppercase leading-none"
        >
          {duel.current_answer ?? "—"}
        </p>
      </div>

      <div className="flex items-center gap-4">
        {running ? (
          <>
            <button
              type="button"
              onClick={() => send({ type: "judge_correct" })}
              className="flex-1 rounded-xl bg-emerald-500/25 py-6 font-display text-3xl uppercase"
            >
              Верно
            </button>
            <button
              type="button"
              onClick={() => send({ type: "judge_pass" })}
              className="flex-1 rounded-xl bg-white/10 py-6 font-display text-3xl uppercase"
            >
              Пас
            </button>
            <button
              type="button"
              onClick={pauseOrResume}
              className="flex-1 rounded-xl bg-amber-500/20 py-6 font-display text-3xl uppercase"
            >
              {paused ? "Продолжить" : "Пауза"}
            </button>
          </>
        ) : (
          <button
            type="button"
            onClick={() => send({ type: "start_duel" })}
            className="flex-1 rounded-xl bg-white/15 py-6 font-display text-3xl uppercase"
          >
            Начать дуэль
          </button>
        )}
        {/* §9.2: «Отмена рядом, но визуально тише». */}
        <button
          type="button"
          data-testid="undo"
          data-quiet="true"
          onClick={() => send({ type: "undo_last_judgement" })}
          className="rounded-lg px-4 py-2 text-sm text-stage-muted underline"
        >
          Отмена
        </button>
      </div>
    </section>
  );
}
```

`frontend/src/widgets/judging/index.ts`: `export { JudgingPanel } from "./ui/judging-panel";`

- [ ] **Step 9: Run it and watch it pass**

Expected: PASS, 9 tests.

- [ ] **Step 10: Write the failing page test — `frontend/src/pages/host-match/ui/match-page.test.tsx`**

```tsx
import { act, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { fakeSocketFactory } from "../../../../testing/fake-socket";
import { hostDuel, hostFrame } from "../../../../testing/host-frames";
import { renderWithQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { MatchPage } from "./match-page";

const MATCH = "33333333-3333-3333-3333-333333333333";

function mount() {
  server.use(
    http.get(`/api/matches/${MATCH}`, () =>
      HttpResponse.json({ frame: hostFrame(), stage_token: "tok-123" }),
    ),
    http.get("/api/library/categories", () => HttpResponse.json([])),
  );
  const { factory, sockets } = fakeSocketFactory();
  renderWithQuery(<MatchPage matchId={MATCH} socketFactory={factory} />);
  act(() => {
    sockets[0]?.open();
  });
  return {
    send: (frame: unknown) =>
      act(() => {
        sockets[0]?.deliver(frame);
      }),
  };
}

describe("MatchPage", () => {
  it("waits before the first frame rather than drawing an empty console", () => {
    mount();
    expect(screen.getByText("Подключение")).toBeInTheDocument();
  });

  it("shows setup before the match has started", () => {
    const { send } = mount();
    send(hostFrame({ status: "setup" }));
    expect(screen.getByRole("button", { name: "Добавить игрока" })).toBeInTheDocument();
  });

  it("shows the board between duels", () => {
    const { send } = mount();
    send(hostFrame({ status: "running", duel: null }));
    expect(screen.getByTestId("group-g1")).toBeInTheDocument();
  });

  it("switches to judging when a duel arrives", () => {
    const { send } = mount();
    send(hostFrame({ status: "running", duel: null }));
    send(hostFrame({ status: "running", duel: hostDuel({ phase: "running" }) }));
    expect(screen.getByTestId("answer")).toHaveTextContent("Титаник");
    expect(screen.queryByTestId("group-g1")).toBeNull();
  });

  it("shows a refusal the operator can read", () => {
    // §6.3, and the reason `useHostMatch` keeps the ack at all.
    const { send } = mount();
    send(hostFrame({ status: "running", duel: null }));
    send({ kind: "ack", correlation_id: "c1", outcome: "rejected", reason: "not_adjacent" });
    expect(screen.getByText(/not_adjacent/)).toBeInTheDocument();
  });

  it("renders the answer, which is the console's alone to hold", () => {
    // The pairing test for the stage plan's R8: `HostDuelFrame` carries
    // `current_answer` and `StageDuelFrame` has no such field, so this is
    // the only surface it can ever reach.
    const { send } = mount();
    send(hostFrame({ status: "running", duel: hostDuel({ phase: "running" }) }));
    expect(screen.getByTestId("answer")).toHaveTextContent("Титаник");
  });
});
```

- [ ] **Step 11: Run it, watch it fail, then write `frontend/src/pages/host-match/ui/match-page.tsx`**

```tsx
import { useQuery } from "@tanstack/react-query";
import { hostBeatOf } from "@/entities/match";
import { useHostMatch } from "@/features/host-commands";
import type { HostSocketFactory, SnapshotBody } from "@/shared/api";
import { useAnimationFrame } from "@/shared/lib/use-animation-frame";
import { useServerClock } from "@/shared/lib/use-server-clock";
import { HostBoard } from "@/widgets/host-board";
import { JudgingPanel } from "@/widgets/judging";
import { MatchSetup } from "@/widgets/match-setup";

export interface MatchPageProps {
  matchId: string;
  socketFactory?: HostSocketFactory;
}

export function MatchPage({ matchId, socketFactory }: MatchPageProps) {
  // H6: the snapshot is fetched only for the stage token, which the socket
  // never carries. Match state comes from the socket and nowhere else.
  const snapshot = useQuery({
    queryKey: ["match-snapshot", matchId],
    queryFn: async (): Promise<SnapshotBody> => {
      const response = await fetch(`/api/matches/${matchId}`);
      if (!response.ok) throw new Error(`snapshot: ${response.status}`);
      return await response.json();
    },
    staleTime: Number.POSITIVE_INFINITY,
  });

  const { frame, refusal, send } = useHostMatch(matchId, socketFactory);
  const now = useServerClock(frame?.server_now ?? null);
  const duel = frame === null ? null : frame.duel;
  useAnimationFrame(duel !== null && !duel.timing.paused);

  if (frame === null) return <p className="p-8 text-stage-muted">Подключение</p>;

  const beat = hostBeatOf(frame);

  return (
    <div className="flex h-full min-h-0 flex-col" data-beat={beat}>
      {refusal && (
        <p className="bg-amber-500/15 px-6 py-2 text-amber-300">
          {refusal.reason ?? refusal.outcome}
          {refusal.message ? ` — ${refusal.message}` : ""}
        </p>
      )}
      <div className="min-h-0 flex-1">
        {beat === "setup" && (
          <MatchSetup
            frame={frame}
            matchId={matchId}
            stageToken={snapshot.data?.stage_token ?? ""}
          />
        )}
        {beat === "board" && (
          <HostBoard
            frame={frame}
            onDeclare={(attacking, defending) =>
              send({
                type: "declare_attack",
                attacking_group: attacking,
                defending_group: defending,
              })
            }
          />
        )}
        {beat === "judging" && duel !== null && (
          <JudgingPanel frame={frame} duel={duel} now={now} send={send} />
        )}
        {beat === "over" && <p className="p-8 font-display text-4xl uppercase">Игра окончена</p>}
      </div>
    </div>
  );
}
```

`frontend/src/pages/host-match/index.ts`: `export { MatchPage } from "./ui/match-page";`

- [ ] **Step 12: Replace the route stub — `frontend/src/app/routes/host.match.$matchId.tsx`**

```tsx
import { createFileRoute } from "@tanstack/react-router";
import { MatchPage } from "@/pages/host-match";

export const Route = createFileRoute("/host/match/$matchId")({ component: RouteComponent });

function RouteComponent() {
  const { matchId } = Route.useParams();
  return <MatchPage matchId={matchId} />;
}
```

- [ ] **Step 13: Green everything and commit**

```bash
cd frontend && pnpm exec vite build; echo "exit=$?"
pnpm build; echo "exit=$?"
pnpm check; echo "exit=$?"
pnpm test; echo "exit=$?"
cd .. && git add frontend && git commit -m "feat(host): the judging screen, at the tempo §9.2 asks for"
```

---

## Task 7: The bundle split, and the review pass

**Files:**
- Create: `frontend/scripts/assert-route-split.mjs`
- Modify: `frontend/package.json`, `.github/workflows/ci.yml`

- [ ] **Step 1: Write `frontend/scripts/assert-route-split.mjs`**

H8: the projector must not download the console. This reads the built chunks and fails if the stage chunk reaches console code.

```js
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const ASSETS = join(import.meta.dirname, "..", "dist", "assets");
// Text that exists only in the console. If any of it appears in the chunk
// the stage route loads, the two route trees have been merged and the
// projector is downloading answers it never renders (§9, H8).
const CONSOLE_ONLY = ["judge_correct", "Начать дуэль", "current_answer"];

const files = readdirSync(ASSETS);
const stageChunk = files.find((name) => /^stage\..*\.js$/.test(name));
if (!stageChunk) {
  console.error(`no stage chunk in ${ASSETS}; found:\n  ${files.join("\n  ")}`);
  process.exit(1);
}

const source = readFileSync(join(ASSETS, stageChunk), "utf8");
const leaked = CONSOLE_ONLY.filter((needle) => source.includes(needle));
if (leaked.length > 0) {
  console.error(`${stageChunk} carries console-only code: ${leaked.join(", ")}`);
  process.exit(1);
}

console.log(`ok: ${stageChunk} is free of console code`);
```

- [ ] **Step 2: Add the script and wire it into CI**

In `frontend/package.json`: `"check:bundle": "vite build && node scripts/assert-route-split.mjs"`.

In `.github/workflows/ci.yml`'s `frontend` job, add `- run: pnpm check:bundle` after `pnpm test`.

- [ ] **Step 3: Run it**

```bash
cd frontend && pnpm check:bundle; echo "exit=$?"
```

Expected: exit 0 and `ok: stage.<hash>.js is free of console code`. If the stage chunk is not named `stage.*`, correct the regex to whatever `autoCodeSplitting` actually emits — do **not** weaken the check to a pattern that matches nothing, which would make it pass by finding no chunk at all. (The `!stageChunk` branch already guards that, so a wrong pattern fails loudly; keep it that way.)

- [ ] **Step 4: Mutation pass**

For each row: apply the mutation to the source, run `pnpm test`, confirm the named tests fail, then `git checkout -- <file>` and confirm green again before the next.

| Mutation | Must kill |
| --- | --- |
| `useHotkeys` — match `event.key` instead of `event.code` | "fires on the physical key, whatever the layout reports" |
| `useHotkeys` — drop the `isTyping()` guard | "does not fire while a text field has focus" |
| `HostBoard` — render every group as enabled | "offers only groups that can attack as a first click" |
| `HostBoard` — call `onDeclare` without checking `targets.includes` | "cannot declare an illegal attack even when the click is forced" |
| `useHostMatch` — never set `refusal` | "surfaces a rejection so the operator can read it", and the page's refusal test |
| `useHostMatch` — set `frame` optimistically inside `send` | "does not touch the frame when a command is sent" |
| `connectHost` — retry on close code 1008 | "does not reconnect after an authentication refusal" |
| `connectHost` — route messages by arrival order rather than `kind` | "routes frames and acks down separate channels" |
| `hostBeatOf` — require `phase === "running"` for judging | "is judging as soon as an attack is declared" |
| `hostBeatOf` — check `finished` last instead of first | "is over once the match finishes, whatever else the frame carries" |
| `JudgingPanel` — always send `pause_duel` | "resumes rather than pauses when the duel is already paused" |
| `useLibraryMutation` — drop the `invalidateQueries` | "refetches the category it changed" |
| `useAuthGate` — report "in" without probing | "is out when the probe is refused" |
| `MatchSetup` — list every category as a secret option | "offers only secret categories as a player's secret" |
| `useDeal` — throw on a 409 | "surfaces a refusal instead of throwing" |
| `MatchSetup` — hide the deal button once groups exist | "offers a redeal once a board exists, rather than hiding the button" |

- [ ] **Step 5: Add H7's missing test**

H7 has no test at all: nothing stops the selection store growing into a second copy of match state. Write it.

```ts
// frontend/src/features/host-commands/model/selection.test.ts
import { describe, expect, it } from "vitest";
import { useSelection } from "./selection";

describe("useSelection", () => {
  it("holds the selected group and nothing else", () => {
    // H7. Kills on: adding frame-derived state here — match state has
    // exactly one source, and it is the socket (H1). If this fails
    // because a field was added deliberately, the ruling is what needs
    // changing, not the assertion.
    expect(Object.keys(useSelection.getState()).sort()).toEqual(["clear", "select", "selected"]);
  });

  it("clears back to nothing", () => {
    useSelection.getState().select("g1");
    expect(useSelection.getState().selected).toBe("g1");
    useSelection.getState().clear();
    expect(useSelection.getState().selected).toBeNull();
  });
});
```

- [ ] **Step 6: Close every survivor**

A mutation the suite survives is a missing test, not a passing one. Write the test that kills it, confirm it fails under the mutation and passes without it, and report it prominently. Do not rationalise a survivor as "not worth testing".

- [ ] **Step 7: Confirm clean and green**

```bash
cd frontend && pnpm build; echo "exit=$?"
pnpm check; echo "exit=$?"
pnpm test; echo "exit=$?"
pnpm check:bundle; echo "exit=$?"
cd .. && git status --porcelain
```

Expected: all 0, and `git status` reporting nothing but intended additions — every mutation reverted.

- [ ] **Step 8: Commit**

```bash
git add frontend .github/workflows/ci.yml
git commit -m "test(host): assert the route split, and close the gaps the mutation pass found"
```

---

## Deliberately not built

- **Image reordering.** `useReorderImages` wraps the route and is exported,
  but no screen calls it: §9.2's «Подготовка» asks for selection from the
  library, not for ordering within a category, and §3.5 draws the pack in
  stored order without the operator needing to curate it live. The hook is
  there so the screen that wants it later costs one component, not one
  round trip through the API layer.
- **Editing an image's picture.** `useEditImage` is used for the answer
  text only; replacing the bytes means uploading a new file, which is
  `useAddImage` plus `useSetImageActive` on the old row — the soft-delete
  path §5.3 already specifies.
## Done when

- `pnpm build`, `pnpm check`, `pnpm test` and `pnpm check:bundle` are all green in `frontend/`, and CI runs all four.
- `/host` covers §9.2's three screens: setup (players, colours, secrets, deal and redeal), the between-duels board, and judging.
- An illegal attack is unreachable on the board rather than refused after the click (H2).
- Space, `P`, `Esc` and `Ctrl+Z` work by physical key, and do nothing while a text field has focus (H4, H5).
- No module writes match state except a frame arriving off the socket (H1).
- The stage chunk contains no console code (H8).
- The word "Podvinsya" appears nowhere under `frontend/` except `contracts.ts`'s generation header.
- The branch `feature/host` is left local — unpushed and unmerged.
