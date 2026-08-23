// The public API of the `api` segment. `contracts` is re-exported with a
// star so the generated file stays the single source of truth: nothing
// here has to be kept in sync when `budge export-types` runs again.
export * from "./contracts";
export {
  connectHost,
  type HostChannel,
  type HostCommand,
  type HostConnection,
  type HostSocketFactory,
  type HostSocketLike,
  type HostStatus,
} from "./host-socket";
export type { SocketFactory, StageConnection, WebSocketLike } from "./stage-socket";
export { connectStage } from "./stage-socket";
