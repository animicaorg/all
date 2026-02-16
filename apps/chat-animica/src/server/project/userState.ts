import { env } from "@/src/server/env";
import { readJsonFile, writeJsonFile } from "@/src/server/storage/jsonFileStore";

type UserSettings = { mode: "strict" | "possibility" };
type MemoryRevision = { ts: number; text: string; version: number };
type UserMemory = { latest: string; revisions: MemoryRevision[] };
type KnowledgePackState = { status: "idle" | "building" | "ready" | "failed"; lastBuiltAt?: string; error?: string };

type UserState = {
  settings: UserSettings;
  memory: UserMemory;
  knowledgePack: KnowledgePackState;
};

type Store = Record<string, UserState>;

const initialState: UserState = {
  settings: { mode: "strict" },
  memory: { latest: "", revisions: [] },
  knowledgePack: { status: "idle" }
};

async function load() {
  return readJsonFile<Store>(env.PROJECT_MEMORY_FILE);
}

async function save(state: Store) {
  return writeJsonFile(env.PROJECT_MEMORY_FILE, state);
}

export async function getUserState(userId: string): Promise<UserState> {
  const state = await load();
  return state[userId] ?? initialState;
}

export async function updateMode(userId: string, mode: "strict" | "possibility") {
  const state = await load();
  const existing = state[userId] ?? initialState;
  state[userId] = { ...existing, settings: { mode } };
  await save(state);
  return state[userId];
}

export async function saveProjectMemory(userId: string, text: string) {
  const state = await load();
  const existing = state[userId] ?? initialState;
  const version = (existing.memory.revisions.at(-1)?.version ?? 0) + 1;
  const revisions = [...existing.memory.revisions, { ts: Date.now(), text, version }].slice(-10);
  state[userId] = { ...existing, memory: { latest: text, revisions } };
  await save(state);
  return state[userId].memory;
}

export async function setKnowledgePackState(userId: string, next: KnowledgePackState) {
  const state = await load();
  const existing = state[userId] ?? initialState;
  state[userId] = { ...existing, knowledgePack: next };
  await save(state);
  return state[userId].knowledgePack;
}
