import { create } from "zustand";
import { filesApi, type TreeNode } from "@/services/filesApi";

export interface FileEntry {
  content?: string;
  dirty: boolean;
  loading: boolean;
  truncated?: boolean;
  saving?: boolean;
  error?: string | null;
}

interface FilesState {
  tree: TreeNode | null;
  treeLoading: boolean;
  treeError: string | null;
  byPath: Record<string, FileEntry>;
  openTabs: string[];
  activePath: string | null;

  loadTree: () => Promise<void>;
  openFile: (path: string) => Promise<void>;
  setActive: (path: string) => void;
  setContent: (path: string, content: string) => void;
  save: (path: string) => Promise<void>;
  closeTab: (path: string) => void;
  reset: () => void;
}

export const useFilesStore = create<FilesState>((set, get) => ({
  tree: null,
  treeLoading: false,
  treeError: null,
  byPath: {},
  openTabs: [],
  activePath: null,

  loadTree: async () => {
    set({ treeLoading: true, treeError: null });
    try {
      const { tree } = await filesApi.tree();
      set({ tree, treeLoading: false });
    } catch (e: any) {
      set({ treeLoading: false, treeError: e?.message ?? "Failed to load files." });
    }
  },

  openFile: async (path: string) => {
    const existing = get().byPath[path];
    // Always make it the active tab; add to tabs if new.
    set((st) => ({
      activePath: path,
      openTabs: st.openTabs.includes(path) ? st.openTabs : [...st.openTabs, path],
    }));
    // If already loaded (and not dirty-resetting), don't refetch.
    if (existing && existing.content !== undefined) return;

    set((st) => ({
      byPath: { ...st.byPath, [path]: { ...(st.byPath[path] ?? {}), dirty: false, loading: true } },
    }));
    try {
      const res = await filesApi.read(path);
      set((st) => ({
        byPath: {
          ...st.byPath,
          [path]: {
            content: res.content,
            truncated: res.truncated,
            dirty: false,
            loading: false,
            error: null,
          },
        },
      }));
    } catch (e: any) {
      set((st) => ({
        byPath: {
          ...st.byPath,
          [path]: { ...(st.byPath[path] ?? { dirty: false }), loading: false, error: e?.message ?? "Failed to open file." },
        },
      }));
    }
  },

  setActive: (path: string) => set({ activePath: path }),

  setContent: (path: string, content: string) =>
    set((st) => {
      const entry = st.byPath[path];
      if (!entry || entry.content === content) return st;
      return {
        byPath: { ...st.byPath, [path]: { ...entry, content, dirty: true } },
      };
    }),

  save: async (path: string) => {
    const entry = get().byPath[path];
    if (!entry || entry.content === undefined) return;
    set((st) => ({ byPath: { ...st.byPath, [path]: { ...st.byPath[path], saving: true, error: null } } }));
    try {
      await filesApi.write(path, entry.content);
      set((st) => ({
        byPath: { ...st.byPath, [path]: { ...st.byPath[path], dirty: false, saving: false } },
      }));
    } catch (e: any) {
      set((st) => ({
        byPath: { ...st.byPath, [path]: { ...st.byPath[path], saving: false, error: e?.message ?? "Failed to save." } },
      }));
      throw e;
    }
  },

  closeTab: (path: string) =>
    set((st) => {
      const tabs = st.openTabs.filter((p) => p !== path);
      let active = st.activePath;
      if (active === path) active = tabs.length ? tabs[tabs.length - 1] : null;
      return { openTabs: tabs, activePath: active };
    }),

  reset: () =>
    set({
      tree: null,
      treeLoading: false,
      treeError: null,
      byPath: {},
      openTabs: [],
      activePath: null,
    }),
}));
