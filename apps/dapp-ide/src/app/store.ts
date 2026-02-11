/**
 * Project Store - Zustand state management for IDE project
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import * as db from "../project/storage/db";

export type ProjectFileType = "python" | "json" | "text";

export interface ProjectFile {
  path: string;
  content: string;
  type: ProjectFileType;
}

export interface CompileArtifact {
  ir: Uint8Array;
  codeHash?: string;
  codeSize?: number;
  abi?: any;
  manifest?: any;
  diagnostics?: string[];
  gasUpperBound?: number;
  timestamp: number;
}

export interface ProjectState {
  // Project metadata
  projectId: string | null;
  projectName: string;
  
  // Files
  files: Map<string, ProjectFile>;
  activeFile: string | null;
  
  // Build state
  lastBuild: CompileArtifact | null;
  isBuilding: boolean;
  buildError: string | null;
  
  // Deployment state
  deployedAddress: string | null;
  lastDeployTx: string | null;
  
  // Actions
  setProjectName: (name: string) => void;
  createFile: (path: string, content: string, type: ProjectFile["type"]) => void;
  updateFile: (path: string, content: string) => void;
  deleteFile: (path: string) => void;
  setActiveFile: (path: string | null) => void;
  
  setBuildResult: (result: CompileArtifact) => void;
  setBuildError: (error: string | null) => void;
  setBuilding: (building: boolean) => void;
  
  setDeployedAddress: (address: string, txHash: string) => void;
  
  loadProject: (projectId: string) => Promise<void>;
  saveProject: () => Promise<void>;
  newProject: (name: string) => void;
}

export const useProjectStore = create<ProjectState>()(
  persist(
    (set, get) => ({
      // Initial state
      projectId: null,
      projectName: "Untitled Project",
      files: new Map(),
      activeFile: null,
      lastBuild: null,
      isBuilding: false,
      buildError: null,
      deployedAddress: null,
      lastDeployTx: null,

      // Actions
      setProjectName: (name) => set({ projectName: name }),

      createFile: (path, content, type) => {
        const files = new Map(get().files);
        files.set(path, { path, content, type });
        set({ files, activeFile: path });
      },

      updateFile: (path, content) => {
        const files = new Map(get().files);
        const file = files.get(path);
        if (file) {
          files.set(path, { ...file, content });
          set({ files });
        }
      },

      deleteFile: (path) => {
        const files = new Map(get().files);
        files.delete(path);
        const activeFile = get().activeFile === path ? null : get().activeFile;
        set({ files, activeFile });
      },

      setActiveFile: (path) => set({ activeFile: path }),

      setBuildResult: (result) =>
        set({
          lastBuild: result,
          isBuilding: false,
          buildError: null,
        }),

      setBuildError: (error) =>
        set({
          buildError: error,
          isBuilding: false,
        }),

      setBuilding: (building) => set({ isBuilding: building }),

      setDeployedAddress: (address, txHash) =>
        set({
          deployedAddress: address,
          lastDeployTx: txHash,
        }),

      loadProject: async (projectId) => {
        try {
          const project = await db.getProject(projectId);
          if (project) {
            set({
              projectId,
              projectName: project.name,
              files: new Map(project.files.map((f) => [f.path, f])),
              activeFile: null,
            });
          }
        } catch (error) {
          console.error("Failed to load project:", error);
        }
      },

      saveProject: async () => {
        const state = get();
        if (!state.projectId) {
          // Create new project
          const id = await db.createProject({
            name: state.projectName,
            files: Array.from(state.files.values()),
            createdAt: Date.now(),
            updatedAt: Date.now(),
          });
          set({ projectId: id });
        } else {
          // Update existing project
          await db.updateProject(state.projectId, {
            name: state.projectName,
            files: Array.from(state.files.values()),
            updatedAt: Date.now(),
          });
        }
      },

      newProject: (name) => {
        set({
          projectId: null,
          projectName: name,
          files: new Map(),
          activeFile: null,
          lastBuild: null,
          isBuilding: false,
          buildError: null,
          deployedAddress: null,
          lastDeployTx: null,
        });
      },
    }),
    {
      name: "animica-dapp-ide-project",
      // Only persist project metadata, not build results
      partialize: (state) => ({
        projectId: state.projectId,
        projectName: state.projectName,
        activeFile: state.activeFile,
      }),
    }
  )
);
