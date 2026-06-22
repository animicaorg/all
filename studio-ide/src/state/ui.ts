import { create } from "zustand";

export type PanelId = "files" | "editor" | "ena" | "terminal" | "preview";

interface UiState {
  activePanel: PanelId;
  filesDrawerOpen: boolean;
  scmOpen: boolean;
  setPanel: (p: PanelId) => void;
  openFiles: () => void;
  closeFiles: () => void;
  openScm: () => void;
  closeScm: () => void;
}

export const useUiStore = create<UiState>((set) => ({
  activePanel: "editor",
  filesDrawerOpen: false,
  scmOpen: false,
  setPanel: (p) => set({ activePanel: p }),
  openFiles: () => set({ filesDrawerOpen: true }),
  closeFiles: () => set({ filesDrawerOpen: false }),
  openScm: () => set({ scmOpen: true }),
  closeScm: () => set({ scmOpen: false }),
}));
