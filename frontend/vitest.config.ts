import { resolve } from "node:path";
import { defineConfig } from "vitest/config";

const here = import.meta.dirname;

export default defineConfig({
  resolve: { alias: { "@": resolve(here, "src") } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./testing/setup.ts"],
    restoreMocks: true,
    clearMocks: true,
  },
});
