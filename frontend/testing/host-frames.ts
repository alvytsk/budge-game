import type {
  HostDuelFrame,
  HostFrame,
  HostGroupFrame,
  PlayerFrame,
  ResolutionFrame,
  TimingFrame,
} from "@/shared/api";

export const ATTACKER = "11111111-1111-1111-1111-111111111111";
export const DEFENDER = "22222222-2222-2222-2222-222222222222";

export function player(overrides: Partial<PlayerFrame> = {}): PlayerFrame {
  return { id: ATTACKER, name: "Аня", colour: "#e4572e", eliminated: false, ...overrides };
}

export function hostGroup(overrides: Partial<HostGroupFrame> = {}): HostGroupFrame {
  return {
    id: "g1",
    owner: ATTACKER,
    category: { id: "c1", name: "Кино" },
    cells: [{ col: 0, row: 0 }],
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

export function hostDuel(overrides: Partial<HostDuelFrame> = {}): HostDuelFrame {
  return {
    attacker: ATTACKER,
    defender: DEFENDER,
    attacking_group: "g1",
    defending_group: "g2",
    category: { id: "c1", name: "Кино" },
    image_order: ["a".repeat(64), "b".repeat(64)],
    index: 0,
    image_count: 2,
    current_answer: "Титаник",
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
    absorbed_cells: [{ col: 1, row: 0 }],
    ...overrides,
  };
}

export function hostFrame(overrides: Partial<HostFrame> = {}): HostFrame {
  return {
    kind: "host",
    match_id: "33333333-3333-3333-3333-333333333333",
    seq: 7,
    server_now: "2026-08-23T20:00:00Z",
    status: "running",
    board: { width: 3, height: 3 },
    players: [player(), player({ id: DEFENDER, name: "Борис", colour: "#2e86e4" })],
    player_count: 2,
    current_player: ATTACKER,
    round_no: 2,
    groups: [
      hostGroup(),
      hostGroup({
        id: "g2",
        owner: DEFENDER,
        cells: [{ col: 1, row: 0 }],
        category: { id: "c2", name: "Спорт" },
      }),
    ],
    duel: null,
    winner: null,
    last_event_types: [],
    resolution: null,
    legal_attacks: { g1: ["g2"], g2: ["g1"] },
    ...overrides,
  };
}
