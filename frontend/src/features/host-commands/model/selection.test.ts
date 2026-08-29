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
