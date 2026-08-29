import { describe, expect, it } from "vitest";
import { cn } from "./cn";

describe("cn", () => {
  it("lets a later class win a conflict", () => {
    expect(cn("p-2", "p-4")).toBe("p-4");
  });

  it("drops falsy entries", () => {
    expect(cn("p-2", false && "hidden", undefined)).toBe("p-2");
  });
});
