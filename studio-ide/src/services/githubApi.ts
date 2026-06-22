// Thin wrapper over the same-origin broker /api/ide/github/* routes (Contract 2).
import { api } from "@/services/api";

export interface GithubUser {
  login: string;
  name: string | null;
  avatar: string | null;
}

export interface GithubStatus {
  connected: boolean;
  login?: string;
}

export interface Repo {
  full_name: string;
  name: string;
  private: boolean;
  default_branch: string;
  updated_at: string;
}

export interface OpenRepoResult {
  ok: true;
  repo: string;
  branch: string;
}

export const githubApi = {
  status: (): Promise<GithubStatus> => api.get("/api/ide/github/status"),
  connect: (token: string): Promise<GithubUser> =>
    api.post("/api/ide/github/connect", { token }),
  disconnect: (): Promise<{ ok: true }> => api.post("/api/ide/github/disconnect", {}),
  repos: (): Promise<{ repos: Repo[] }> => api.get("/api/ide/github/repos"),
  openRepo: (full_name: string): Promise<OpenRepoResult> =>
    api.post("/api/ide/repo/open", { full_name }),
  repoStatus: (): Promise<RepoStatus> => api.get("/api/ide/repo/status"),
};

export interface RepoStatus {
  branch: string;
  ahead: number;
  behind: number;
  files: { path: string; status: string }[];
}
