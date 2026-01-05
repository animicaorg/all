import { create } from 'zustand'
import type { Project, WorkspaceSession, WorkspaceFile } from '@/types'

interface WorkspaceState {
  projects: Project[]
  activeProjectId: string | null
  session: WorkspaceSession | null
  selectedFile: WorkspaceFile | null
  
  // Actions
  setProjects: (projects: Project[]) => void
  addProject: (project: Project) => void
  setActiveProject: (id: string | null) => void
  setSession: (session: WorkspaceSession | null) => void
  setSelectedFile: (file: WorkspaceFile | null) => void
  updateFile: (path: string, content: string) => void
}

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  projects: [],
  activeProjectId: null,
  session: null,
  selectedFile: null,
  
  setProjects: (projects) => set({ projects }),
  
  addProject: (project) =>
    set((state) => ({
      projects: [project, ...state.projects],
      activeProjectId: project.id,
    })),
  
  setActiveProject: (id) => set({ activeProjectId: id }),
  
  setSession: (session) => set({ session }),
  
  setSelectedFile: (file) => set({ selectedFile: file }),
  
  updateFile: (path, content) =>
    set((state) => {
      if (!state.session) return state
      
      return {
        session: {
          ...state.session,
          files: state.session.files.map((file) =>
            file.path === path ? { ...file, content, modified: true } : file
          ),
        },
        selectedFile:
          state.selectedFile?.path === path
            ? { ...state.selectedFile, content, modified: true }
            : state.selectedFile,
      }
    }),
}))
