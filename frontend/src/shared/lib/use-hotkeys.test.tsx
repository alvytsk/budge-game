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

  it("swallows the browser's own meaning of a key it handles", () => {
    // Space scrolls the page and Ctrl+Z opens the browser's undo. Both
    // happen *in addition* to the handler firing, so nothing about a
    // handler-count assertion notices them. Kills on: dropping
    // `preventDefault` — the operator judges the duel and the console
    // scrolls out from under them at one decision every five seconds.
    renderHook(() => useHotkeys({ Space: vi.fn(), "Ctrl+KeyZ": vi.fn() }, true));

    const space = new KeyboardEvent("keydown", { code: "Space", bubbles: true, cancelable: true });
    document.dispatchEvent(space);
    expect(space.defaultPrevented).toBe(true);

    const undo = new KeyboardEvent("keydown", {
      code: "KeyZ",
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    document.dispatchEvent(undo);
    expect(undo.defaultPrevented).toBe(true);
  });

  it("leaves a key it does not handle alone", () => {
    // The other half: `preventDefault` on everything would break Tab,
    // F5 and the operator's own shortcuts. Only a bound key is swallowed.
    renderHook(() => useHotkeys({ Space: vi.fn() }, true));
    const tab = new KeyboardEvent("keydown", { code: "Tab", bubbles: true, cancelable: true });
    document.dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(false);
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
