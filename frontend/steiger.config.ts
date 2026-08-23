import fsd from "@feature-sliced/steiger-plugin";
import { defineConfig } from "steiger";

export default defineConfig([
  ...fsd.configs.recommended,
  { ignores: ["**/routeTree.gen.ts"] },
  { files: ["./src/**"], rules: { "fsd/import-locality": "error" } },
  // One slice per layer is the intended shape here, not an accident:
  // `pages/stage` is the only page this plan builds, and `entities/duel`
  // exists to keep timer arithmetic out of the widget that draws it.
  { files: ["./src/entities/**", "./src/pages/**"], rules: { "fsd/insignificant-slice": "off" } },
]);
