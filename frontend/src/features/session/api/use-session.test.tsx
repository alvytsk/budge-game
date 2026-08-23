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
