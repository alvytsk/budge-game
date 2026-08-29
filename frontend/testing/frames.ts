import type {
  CellFrame,
  PlayerFrame,
  ResolutionFrame,
  StageDuelFrame,
  StageFrame,
  StageGroupFrame,
  TimingFrame,
} from "@/shared/api/contracts";

export const ATTACKER = "11111111-1111-1111-1111-111111111111";
export const DEFENDER = "22222222-2222-2222-2222-222222222222";

export function cells(...pairs: [number, number][]): CellFrame[] {
  return pairs.map(([col, row]) => ({ col, row }));
}

export function player(overrides: Partial<PlayerFrame> = {}): PlayerFrame {
  return { id: ATTACKER, name: "Аня", colour: "#e4572e", eliminated: false, ...overrides };
}

export function group(overrides: Partial<StageGroupFrame> = {}): StageGroupFrame {
  return {
    id: "g1",
    owner: ATTACKER,
    category: { kind: "named", name: "Кино" },
    cells: cells([0, 0]),
    revealed: true,
    ...overrides,
  };
}

export function timing(overrides: Partial<TimingFrame> = {}): TimingFrame {
  return {
    remaining_ms: { [ATTACKER]: 60_000, [DEFENDER]: 60_000 },
    answering: ATTACKER,
    anchor: "2026-08-23T20:00:00Z",
    paused: false,
    deadline_at: "2026-08-23T20:01:00Z",
    ...overrides,
  };
}

export function duel(overrides: Partial<StageDuelFrame> = {}): StageDuelFrame {
  return {
    attacker: ATTACKER,
    defender: DEFENDER,
    attacking_group: "g1",
    defending_group: "g2",
    category: { kind: "named", name: "Кино" },
    image_order: ["a".repeat(64), "b".repeat(64)],
    index: 0,
    phase: "declared",
    timing: timing(),
    ...overrides,
  };
}

export function resolution(overrides: Partial<ResolutionFrame> = {}): ResolutionFrame {
  return {
    winner: ATTACKER,
    loser: DEFENDER,
    surviving_group: "g1",
    absorbed_group: "g2",
    absorbed_cells: cells([1, 0]),
    ...overrides,
  };
}

export function stageFrame(overrides: Partial<StageFrame> = {}): StageFrame {
  return {
    kind: "stage",
    match_id: "33333333-3333-3333-3333-333333333333",
    seq: 7,
    server_now: "2026-08-23T20:00:00Z",
    status: "running",
    board: { width: 3, height: 3 },
    players: [player(), player({ id: DEFENDER, name: "Борис", colour: "#2e86e4" })],
    current_player: ATTACKER,
    round_no: 2,
    groups: [
      group(),
      group({
        id: "g2",
        owner: DEFENDER,
        cells: cells([1, 0]),
        category: { kind: "hidden" },
        revealed: false,
      }),
    ],
    duel: null,
    winner: null,
    last_event_types: [],
    resolution: null,
    ...overrides,
  };
}
