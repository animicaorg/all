import { create } from "zustand";

export type PanelId = "files" | "editor" | "ena" | "terminal" | "preview";

interface UiState {
  activePanel: PanelId;
  filesDrawerOpen: boolean;
  setPanel: (p: PanelId) => void;
  openFiles: () => void;
  closeFiles: () => void;
}

export const useUiStore = create<UiState>((set) => ({
  activePanel: "editor",
  filesDrawerOpen: false,
  setPanel: (p) => set({ activePanel: p }),
  openFiles: () => set({ filesDrawerOpen: true }),
  closeFiles: () => set({ filesDrawerOpen: false }),
}));
