/**
 * IndexedDB wrapper for project persistence
 */

import { openDB } from "idb";
import type { DBSchema, IDBPDatabase } from "idb";
import type { Project, CompiledArtifact } from "../../animica/types";

interface DappIDEDB extends DBSchema {
  projects: {
    key: string;
    value: Project;
    indexes: { "by-updated": number };
  };
  artifacts: {
    key: string; // projectId
    value: CompiledArtifact;
  };
}

const DB_NAME = "animica-dapp-ide";
const DB_VERSION = 1;

let dbInstance: IDBPDatabase<DappIDEDB> | null = null;

/**
 * Initialize and get database instance
 */
async function getDB(): Promise<IDBPDatabase<DappIDEDB>> {
  if (dbInstance) {
    return dbInstance;
  }

  dbInstance = await openDB<DappIDEDB>(DB_NAME, DB_VERSION, {
    upgrade(db) {
      // Projects store
      if (!db.objectStoreNames.contains("projects")) {
        const projectStore = db.createObjectStore("projects", { keyPath: "id" });
        projectStore.createIndex("by-updated", "updatedAt");
      }

      // Artifacts store
      if (!db.objectStoreNames.contains("artifacts")) {
        db.createObjectStore("artifacts", { keyPath: "projectId" });
      }
    },
  });

  return dbInstance;
}

/**
 * Create a new project
 */
export async function createProject(project: Omit<Project, "id">): Promise<string> {
  const db = await getDB();
  const id = crypto.randomUUID();
  const newProject: Project = {
    ...project,
    id,
  };
  await db.put("projects", newProject);
  return id;
}

/**
 * Save a project
 */
export async function saveProject(project: Project): Promise<void> {
  const db = await getDB();
  await db.put("projects", project);
}

/**
 * Update a project
 */
export async function updateProject(id: string, updates: Partial<Project>): Promise<void> {
  const db = await getDB();
  const existing = await db.get("projects", id);
  if (!existing) {
    throw new Error(`Project ${id} not found`);
  }
  
  const updated = {
    ...existing,
    ...updates,
    id,
  };
  
  await db.put("projects", updated);
}

/**
 * Get a project by ID
 */
export async function getProject(id: string): Promise<Project | undefined> {
  const db = await getDB();
  return db.get("projects", id);
}

/**
 * Get all projects
 */
export async function getAllProjects(): Promise<Project[]> {
  const db = await getDB();
  return db.getAllFromIndex("projects", "by-updated");
}

/**
 * Delete a project
 */
export async function deleteProject(id: string): Promise<void> {
  const db = await getDB();
  const tx = db.transaction(["projects", "artifacts"], "readwrite");
  await Promise.all([
    tx.objectStore("projects").delete(id),
    tx.objectStore("artifacts").delete(id),
    tx.done,
  ]);
}

/**
 * Save compiled artifact
 */
export async function saveArtifact(
  projectId: string,
  artifact: CompiledArtifact
): Promise<void> {
  const db = await getDB();
  await db.put("artifacts", { ...artifact, projectId } as any);
}

/**
 * Get compiled artifact
 */
export async function getArtifact(
  projectId: string
): Promise<CompiledArtifact | undefined> {
  const db = await getDB();
  return db.get("artifacts", projectId);
}
