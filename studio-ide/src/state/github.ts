import { create } from "zustand";
import { ApiError } from "@/services/api";
import { githubApi, type Repo } from "@/services/githubApi";
import { useFilesStore } from "@/state/files";

interface GithubState {
  connected: boolean;
  login: string | null;
  repos: Repo[];
  currentRepo: string | null;
  currentBranch: string | null;
  loading: boolean;
  reposLoading: boolean;
  opening: boolean;
  error: string | null;

  status: () => Promise<void>;
  connect: (token: string) => Promise<void>;
  disconnect: () => Promise<void>;
  listRepos: () => Promise<void>;
  openRepo: (full_name: string) => Promise<void>;
  closeRepo: () => void;
  clearError: () => void;
}

export const useGithubStore = create<GithubState>((set, get) => ({
  connected: false,
  login: null,
  repos: [],
  currentRepo: null,
  currentBranch: null,
  loading: true,
  reposLoading: false,
  opening: false,
  error: null,

  status: async () => {
    set({ loading: true, error: null });
    try {
      const s = await githubApi.status();
      set({ connected: !!s.connected, login: s.login ?? null, loading: false });
    } catch (e: any) {
      set({ connected: false, login: null, loading: false, error: e?.message ?? null });
    }
  },

  connect: async (token: string) => {
    set({ loading: true, error: null });
    try {
      const user = await githubApi.connect(token.trim());
      set({ connected: true, login: user.login, loading: false });
      await get().listRepos();
    } catch (e: any) {
      const msg =
        e instanceof ApiError && e.status === 401
          ? "That token was rejected by GitHub. Check it has the 'repo' scope."
          : e?.message ?? "Failed to connect.";
      set({ loading: false, error: msg });
      throw e;
    }
  },

  disconnect: async () => {
    try {
      await githubApi.disconnect();
    } catch {
      /* ignore */
    }
    useFilesStore.getState().reset();
    set({
      connected: false,
      login: null,
      repos: [],
      currentRepo: null,
      currentBranch: null,
      error: null,
    });
  },

  listRepos: async () => {
    set({ reposLoading: true, error: null });
    try {
      const { repos } = await githubApi.repos();
      set({ repos, reposLoading: false });
    } catch (e: any) {
      set({ reposLoading: false, error: e?.message ?? "Failed to load repositories." });
    }
  },

  openRepo: async (full_name: string) => {
    set({ opening: true, error: null });
    try {
      const res = await githubApi.openRepo(full_name);
      set({ currentRepo: res.repo, currentBranch: res.branch, opening: false });
      // Reset and load the file tree for the freshly cloned repo.
      const files = useFilesStore.getState();
      files.reset();
      await files.loadTree();
    } catch (e: any) {
      set({ opening: false, error: e?.message ?? "Failed to open repository." });
      throw e;
    }
  },

  closeRepo: () => {
    useFilesStore.getState().reset();
    set({ currentRepo: null, currentBranch: null });
  },

  clearError: () => set({ error: null }),
}));
