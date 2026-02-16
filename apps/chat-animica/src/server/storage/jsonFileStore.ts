import fs from "fs/promises";
import path from "path";

async function ensureFile(filePath: string) {
  const absolute = path.isAbsolute(filePath) ? filePath : path.join(process.cwd(), filePath);
  await fs.mkdir(path.dirname(absolute), { recursive: true });
  try {
    await fs.access(absolute);
  } catch {
    await fs.writeFile(absolute, "{}", "utf8");
  }
  return absolute;
}

export async function readJsonFile<T>(filePath: string): Promise<T> {
  const absolute = await ensureFile(filePath);
  const raw = await fs.readFile(absolute, "utf8");
  return JSON.parse(raw) as T;
}

export async function writeJsonFile<T>(filePath: string, value: T) {
  const absolute = await ensureFile(filePath);
  await fs.writeFile(absolute, JSON.stringify(value, null, 2), "utf8");
}
