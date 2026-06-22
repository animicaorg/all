import { create } from "zustand";
import { gitApi } from "@/services/gitApi";
import { useFilesStore } from "@/state/files";

export interface ChangedFile {
  path: string;
  status: string;
}

interface ScmState {
  branch: string | null;
  ahead: number;
  behind: number;
  changedFiles: ChangedFile[];
  diffByPath: Record<string, string>;
  loading: boolean;
  committing: boolean;
  pushing: boolean;
  error: string | null;
  notice: string | null;

  refresh: () => Promise<void>;
  loadDiff: (path: string) => Promise<string>;
  commit: (message: string) => Promise<boolean>;
  push: () => Promise<boolean>;
  clearNotice: () => void;
  reset: () => void;
}

export const useScmStore = create<ScmState>((set, get) => ({
  branch: null,
  ahead: 0,
  behind: 0,
  changedFiles: [],
  diffByPath: {},
  loading: false,
  committing: false,
  pushing: false,
  error: null,
  notice: null,

  refresh: async () => {
    set({ loading: true, error: null });
    try {
      const s = await gitApi.status();
      set({
        branch: s.branch ?? null,
        ahead: s.ahead ?? 0,
        behind: s.behind ?? 0,
        changedFiles: s.files ?? [],
        diffByPath: {}, // invalidate cached diffs on refresh
        loading: false,
      });
    } catch (e: any) {
      set({ loading: false, error: e?.message ?? "Failed to load changes." });
    }
  },

  loadDiff: async (path: string) => {
    const cached = get().diffByPath[path];
    if (cached !== undefined) return cached;
    try {
      const { diff } = await gitApi.diff(path);
      set((st) => ({ diffByPath: { ...st.diffByPath, [path]: diff ?? "" } }));
      return diff ?? "";
    } catch (e: any) {
      const msg = `# Failed to load diff: ${e?.message ?? "error"}`;
      set((st) => ({ diffByPath: { ...st.diffByPath, [path]: msg } }));
      return msg;
    }
  },

  commit: async (message: string) => {
    const msg = message.trim();
    if (!msg) {
      set({ error: "Enter a commit message." });
      return false;
    }
    set({ committing: true, error: null, notice: null });
    try {
      const res = await gitApi.commit(msg);
      if (!res.ok) {
        set({ committing: false, error: res.error ?? "Nothing to commit." });
        return false;
      }
      set({ committing: false, notice: `Committed ${res.commit ? res.commit.slice(0, 7) : ""}`.trim() });
      await get().refresh();
      // Saved buffers are now clean against HEAD; refresh the tree too.
      void useFilesStore.getState().loadTree();
      return true;
    } catch (e: any) {
      set({ committing: false, error: e?.message ?? "Commit failed." });
      return false;
    }
  },

  push: async () => {
    set({ pushing: true, error: null, notice: null });
    try {
      const res = await gitApi.push();
      if (!res.ok) {
        set({ pushing: false, error: res.error ?? "Push failed." });
        return false;
      }
      set({ pushing: false, notice: `Pushed ${res.branch ?? ""}`.trim() });
      await get().refresh();
      return true;
    } catch (e: any) {
      set({ pushing: false, error: e?.message ?? "Push failed." });
      return false;
    }
  },

  clearNotice: () => set({ notice: null, error: null }),

  reset: () =>
    set({
      branch: null,
      ahead: 0,
      behind: 0,
      changedFiles: [],
      diffByPath: {},
      loading: false,
      committing: false,
      pushing: false,
      error: null,
      notice: null,
    }),
}));
