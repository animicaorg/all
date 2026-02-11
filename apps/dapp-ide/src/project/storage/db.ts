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
 * Save a project
 */
export async function saveProject(project: Project): Promise<void> {
  const db = await getDB();
  await db.put("projects", project);
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

/**
 * Create a new project
 */
export function createProject(name: string, description?: string): Project {
  const now = Date.now();
  return {
    id: `project-${now}-${Math.random().toString(36).substr(2, 9)}`,
    name,
    description: description || "",
    createdAt: now,
    updatedAt: now,
    files: [
      {
        path: "src/main.py",
        content: "# Write your contract here\n",
        type: "python",
        lastModified: now,
      },
      {
        path: "manifest.json",
        content: JSON.stringify(
          {
            manifestVersion: "1.0.0",
            encoding: "animica-manifest/1",
            package: {
              name: name.toLowerCase().replace(/\s+/g, "-"),
              version: "0.1.0",
              description: description || "",
            },
            target: {
              vm: "python",
              vmVersion: "1.0.0",
              abiVersion: "1.0.0",
            },
            entrypoint: "src/main.py",
          },
          null,
          2
        ),
        type: "json",
        lastModified: now,
      },
    ],
  };
}
