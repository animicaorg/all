// Run & preview client. Talks to the same-origin broker routes which proxy the
// sidecar /preview/* endpoints (single dev server per container). The dev
// server itself is reached through the broker proxy at /api/ide/preview/app/.
import { api } from "@/services/api";

export interface PreviewStart {
  ok: boolean;
  url: string;
  pid: number;
  cmd: string;
}

export interface PreviewStatus {
  running: boolean;
  cmd?: string;
  port: number;
  // True when the dev-server port is accepting connections inside the container.
  listening?: boolean;
  logTail?: string;
}

// The browser-facing URL of the running dev server (proxied by the broker).
export const PREVIEW_APP_URL = "/api/ide/preview/app/";

export const previewApi = {
  start: (cmd?: string): Promise<PreviewStart> =>
    api.post("/api/ide/preview/start", cmd ? { cmd } : {}),
  stop: (): Promise<{ ok: boolean }> => api.post("/api/ide/preview/stop", {}),
  status: (): Promise<PreviewStatus> => api.get("/api/ide/preview/status"),
};
