import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const ASSETS = join(import.meta.dirname, "..", "dist", "assets");
// Text that exists only in the console. H8: the projector must not
// download the answers, so this may live ONLY in a host chunk — checking
// the stage chunk alone would miss the case that actually matters, where
// console code lands in the shared entry chunk that both routes load.
const CONSOLE_ONLY = ["judge_correct", "Начать дуэль", "current_answer"];
const isHostChunk = (name) => name.startsWith("host.");

const chunks = readdirSync(ASSETS).filter((name) => name.endsWith(".js"));
if (!chunks.some(isHostChunk)) {
  console.error(`no host chunk in ${ASSETS}; found:\n  ${chunks.join("\n  ")}`);
  process.exit(1);
}

const problems = [];
for (const needle of CONSOLE_ONLY) {
  const carriers = chunks.filter((name) =>
    readFileSync(join(ASSETS, name), "utf8").includes(needle),
  );
  const strays = carriers.filter((name) => !isHostChunk(name));
  if (strays.length > 0) {
    problems.push(`${needle} reached non-console chunks: ${strays.join(", ")}`);
  } else if (carriers.length === 0) {
    // A needle nothing contains makes this check vacuous — most likely
    // the console stopped using that string, not that the split improved.
    problems.push(`${needle} appears in no chunk at all; the check is stale`);
  }
}

if (problems.length > 0) {
  console.error(problems.join("\n"));
  process.exit(1);
}

console.log(`ok: console code is confined to ${chunks.filter(isHostChunk).join(", ")}`);
