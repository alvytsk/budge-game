import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./server";

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

if (!globalThis.URL.createObjectURL) {
  globalThis.URL.createObjectURL = () => "blob:budge";
  globalThis.URL.revokeObjectURL = () => {};
}

// `onUnhandledRequest: "error"` is the point: an unmocked call is a test
// silently exercising nothing.
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
