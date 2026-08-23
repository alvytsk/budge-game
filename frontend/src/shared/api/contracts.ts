// Generated from the Pydantic models by `podvinsya export-types`.
// Do not edit: run `podvinsya export-types` and commit the result.
// The CI job `contracts` fails if this file and the models disagree (§7.6).

export interface Ack {
  kind: "ack";
  correlation_id: string | null;
  outcome: "accepted" | "noop" | "rejected" | "failed" | "malformed";
  reason?: string | null;
  message?: string | null;
}

export interface AddImageBody {
  /** pattern: ^[0-9a-f]{64}$ */
  media_sha256: string;
  answer_text: string;
}

export interface AddPlayerBody {
  player_id: string;
  name: string;
  colour: string;
}

export interface AssignSecretBody {
  player_id: string;
  category: string;
}

export interface BoardBody {
  width: number;
  height: number;
}

export interface BoardFrame {
  width: number;
  height: number;
}

export interface CategoryDetailBody {
  category: CategorySummaryBody;
  images: ImageBody[];
}

export interface CategorySummaryBody {
  id: string;
  title: string;
  is_secret: boolean;
  is_active: boolean;
  version: number;
  active_image_count: number;
}

export interface CellFrame {
  col: number;
  row: number;
}

export interface CreateCategoryBody {
  title: string;
  is_secret?: boolean;
}

export interface CreateMatchBody {
  board: BoardBody;
  settings?: SettingsBody;
  player_count: number;
}

export interface CreatedMatchBody {
  outcome: "accepted" | "noop" | "rejected" | "failed";
  reason?: string | null;
  message?: string | null;
  match_id: string;
  stage_token: string;
}

export interface DeclareAttackCommand {
  type: "declare_attack";
  attacking_group: string;
  defending_group: string;
}

export type DuelPhase = "declared" | "running";

export interface EditCategoryBody {
  title: string;
  is_secret: boolean;
}

export interface EditImageBody {
  /** pattern: ^[0-9a-f]{64}$ */
  media_sha256: string;
  answer_text: string;
}

export interface Envelope {
  correlation_id?: string | null;
  command:
    | DeclareAttackCommand
    | StartDuelCommand
    | JudgeCorrectCommand
    | JudgePassCommand
    | PauseDuelCommand
    | ResumeDuelCommand
    | UndoLastJudgementCommand;
}

export interface HiddenCategory {
  kind: "hidden";
}

export interface HostCategory {
  id: string;
  name: string | null;
}

export interface HostDuelFrame {
  attacker: string;
  defender: string;
  attacking_group: string;
  defending_group: string;
  category: HostCategory;
  image_order: string[];
  index: number;
  image_count: number;
  current_answer: string | null;
  phase: DuelPhase;
  timing: TimingFrame;
}

export interface HostFrame {
  kind: "host";
  match_id: string;
  seq: number;
  server_now: string;
  status: MatchStatus;
  board: BoardFrame;
  players: PlayerFrame[];
  current_player: string | null;
  round_no: number;
  groups: HostGroupFrame[];
  duel: HostDuelFrame | null;
  winner: string | null;
  last_event_types: string[];
  resolution: ResolutionFrame | null;
  legal_attacks: Record<string, string[]>;
}

export interface HostGroupFrame {
  id: string;
  owner: string;
  category: HostCategory;
  cells: CellFrame[];
  revealed: boolean;
}

export interface ImageBody {
  id: string;
  media_sha256: string;
  answer_text: string;
  position: number;
  is_active: boolean;
}

export interface JudgeCorrectCommand {
  type: "judge_correct";
}

export interface JudgePassCommand {
  type: "judge_pass";
}

export interface LoginBody {
  password: string;
}

export type MatchStatus = "setup" | "running" | "finished";

export interface MatchSummaryBody {
  id: string;
  status: string;
  winner_id: string | null;
  last_seq: number;
  players: PlayerSummaryBody[];
}

export interface NamedCategory {
  kind: "named";
  name: string;
}

export interface OutcomeBody {
  outcome: "accepted" | "noop" | "rejected" | "failed";
  reason?: string | null;
  message?: string | null;
}

export interface PauseDuelCommand {
  type: "pause_duel";
}

export interface PlayerFrame {
  id: string;
  name: string;
  colour: string;
  eliminated: boolean;
}

export interface PlayerSummaryBody {
  name: string;
  colour: string;
  eliminated: boolean;
}

export interface ReadinessBody {
  cells: number;
  threshold: number;
  ordinary_available: number;
  secrets_available: number;
  thin: ThinCategoryBody[];
  ready: boolean;
}

export interface ReorderImagesBody {
  image_ids: string[];
}

export interface ResolutionFrame {
  winner: string;
  loser: string;
  surviving_group: string;
  absorbed_group: string;
  absorbed_cells: CellFrame[];
}

export interface ResumeDuelCommand {
  type: "resume_duel";
}

export interface SetActiveBody {
  is_active: boolean;
}

export interface SettingsBody {
  base_seconds?: number;
  bonus_cap_seconds?: number;
  pass_penalty_seconds?: number;
}

export interface SnapshotBody {
  frame: HostFrame;
  stage_token: string;
}

export interface StageDuelFrame {
  attacker: string;
  defender: string;
  attacking_group: string;
  defending_group: string;
  category: NamedCategory | HiddenCategory;
  image_order: string[];
  index: number;
  phase: DuelPhase;
  timing: TimingFrame;
}

export interface StageFrame {
  kind: "stage";
  match_id: string;
  seq: number;
  server_now: string;
  status: MatchStatus;
  board: BoardFrame;
  players: PlayerFrame[];
  current_player: string | null;
  round_no: number;
  groups: StageGroupFrame[];
  duel: StageDuelFrame | null;
  winner: string | null;
  last_event_types: string[];
  resolution: ResolutionFrame | null;
}

export interface StageGroupFrame {
  id: string;
  owner: string;
  category: NamedCategory | HiddenCategory;
  cells: CellFrame[];
  revealed: boolean;
}

export interface StartDuelCommand {
  type: "start_duel";
}

export interface ThinCategoryBody {
  id: string;
  title: string;
  active_image_count: number;
}

export interface TimingFrame {
  remaining_ms: Record<string, number>;
  answering: string;
  anchor: string | null;
  paused: boolean;
  deadline_at: string | null;
}

export interface UndoLastJudgementCommand {
  type: "undo_last_judgement";
}
