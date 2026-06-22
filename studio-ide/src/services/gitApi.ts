// Thin wrapper over the same-origin broker /api/ide/git/* + repo/status routes.
import { api } from "@/services/api";
import { githubApi, type RepoStatus } from "@/services/githubApi";

export interface DiffResult {
  diff: string;
}

export interface CommitResult {
  ok: boolean;
  commit?: string;
  error?: string;
}

export interface PushResult {
  ok: boolean;
  branch?: string;
  error?: string;
  scrubbed?: string;
}

export const gitApi = {
  // git status (reuse the Inc-1 repo/status route).
  status: (): Promise<RepoStatus> => githubApi.repoStatus(),

  diff: (path?: string): Promise<DiffResult> =>
    api.get(path ? `/api/ide/git/diff?path=${encodeURIComponent(path)}` : "/api/ide/git/diff"),

  commit: (message: string, paths?: string[]): Promise<CommitResult> =>
    api.post("/api/ide/git/commit", paths ? { message, paths } : { message }),

  // The broker injects the decrypted PAT server-side; no token from the client.
  push: (): Promise<PushResult> => api.post("/api/ide/git/push", {}),
};
