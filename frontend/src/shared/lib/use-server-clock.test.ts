import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useServerClock } from "./use-server-clock";

describe("useServerClock", () => {
  it("reads the server's clock, not the browser's", () => {
    // Kills on: returning `Date.now()` — a projector whose clock is five
    // minutes off would show a five-minute-wrong timer, the drift §7.3
    // names.
    vi.setSystemTime(new Date("2026-08-23T20:05:00Z"));
    const { result } = renderHook(() => useServerClock("2026-08-23T20:00:00Z"));
    expect(result.current()).toBeCloseTo(Date.parse("2026-08-23T20:00:00Z"), -2);
  });

  it("advances at real time between frames", () => {
    vi.setSystemTime(new Date("2026-08-23T20:05:00Z"));
    const { result } = renderHook(() => useServerClock("2026-08-23T20:00:00Z"));
    vi.setSystemTime(new Date("2026-08-23T20:05:30Z"));
    expect(result.current()).toBeCloseTo(Date.parse("2026-08-23T20:00:30Z"), -2);
  });

  it("re-corrects when a later frame carries a new server_now", () => {
    // Kills on: latching the offset on the first frame. If the server's
    // clock is stepped mid-show, the screen must follow it.
    vi.setSystemTime(new Date("2026-08-23T20:05:00Z"));
    const { result, rerender } = renderHook(({ now }) => useServerClock(now), {
      initialProps: { now: "2026-08-23T20:00:00Z" as string | null },
    });
    rerender({ now: "2026-08-23T21:00:00Z" });
    expect(result.current()).toBeCloseTo(Date.parse("2026-08-23T21:00:00Z"), -2);
  });

  it("falls back to the browser clock before any frame has arrived", () => {
    vi.setSystemTime(new Date("2026-08-23T20:05:00Z"));
    const { result } = renderHook(() => useServerClock(null));
    expect(result.current()).toBeCloseTo(Date.parse("2026-08-23T20:05:00Z"), -2);
  });
});
