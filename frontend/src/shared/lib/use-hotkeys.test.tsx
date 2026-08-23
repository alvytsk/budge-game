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
