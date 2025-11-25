export { VERSION as version, getVersion } from "./version";
export * from "./api/compiler";
export * from "./api/simulator";
export * from "./api/state";
export * from "./api/events";
export * from "./worker/protocol";

// Provide a convenient default export mirroring key APIs.
import * as compiler from "./api/compiler";
import * as simulator from "./api/simulator";
import * as state from "./api/state";
import * as events from "./api/events";
import * as protocol from "./worker/protocol";
import { VERSION } from "./version";

export default {
  version: VERSION,
  ...compiler,
  ...simulator,
  ...state,
  ...events,
  ...protocol,
};
