import fsd from "@feature-sliced/steiger-plugin";
import { defineConfig } from "steiger";

export default defineConfig([
  ...fsd.configs.recommended,
  { ignores: ["**/routeTree.gen.ts"] },
  { files: ["./src/**"], rules: { "fsd/import-locality": "error" } },
  // One slice per layer is the intended shape here, not an accident:
  // `pages/stage` is the only page this plan builds, `entities/duel`
  // exists to keep timer arithmetic out of the widget that draws it, and
  // each widget (`board`, `duel`, `overlay`) is drawn by that one page by
  // design — the stage screen is a single surface split for readability,
  // so "only one reference, consider merging" is advice to undo the split.
  {
    files: ["./src/entities/**", "./src/pages/**", "./src/widgets/**"],
    rules: { "fsd/insignificant-slice": "off" },
  },
  // `segments-by-purpose` blacklists the word "providers" because inside a
  // slice it names a technology rather than a job. The `app` layer is not
  // sliced, and `app/providers` is FSD's own name for exactly this file —
  // the composition root that wraps the tree in its context providers.
  // Scoped to `app` alone, so the rule still governs every real slice.
  { files: ["./src/app/**"], rules: { "fsd/segments-by-purpose": "off" } },
]);
