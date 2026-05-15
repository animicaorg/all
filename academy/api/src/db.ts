import Database from "better-sqlite3";
import { existsSync, mkdirSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export type Db = ReturnType<typeof openDb>;

export function openDb(path: string) {
  const dir = dirname(path);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  const db = new Database(path);
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");
  runMigrations(db);
  return db;
}

function runMigrations(db: Database.Database) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS schema_version (
      version INTEGER PRIMARY KEY
    );
  `);

  const current = (
    db.prepare("SELECT MAX(version) AS v FROM schema_version").get() as
      | { v: number | null }
      | undefined
  )?.v ?? 0;

  const migrationsDir = resolveMigrationsDir();
  const files = readdirSync(migrationsDir)
    .filter((f) => f.endsWith(".sql"))
    .sort();

  for (const file of files) {
    const version = Number(file.split("_")[0]);
    if (!Number.isFinite(version)) continue;
    if (version <= current) continue;
    const sql = readFileSync(join(migrationsDir, file), "utf8");
    db.exec("BEGIN");
    try {
      db.exec(sql);
      db.prepare("INSERT INTO schema_version (version) VALUES (?)").run(version);
      db.exec("COMMIT");
    } catch (e) {
      db.exec("ROLLBACK");
      throw new Error(`migration ${file} failed: ${(e as Error).message}`);
    }
  }
}

function resolveMigrationsDir(): string {
  // Resolves regardless of CWD: src/ (tsx) and dist/ (compiled) both live
  // one directory below the package root, where migrations/ sits. Falls
  // back to a CWD-relative lookup so an explicit override via cwd still
  // works during local development.
  const here = dirname(fileURLToPath(import.meta.url));
  const cwd = process.cwd();
  const candidates = [
    resolve(here, "../migrations"),
    join(cwd, "migrations"),
    join(cwd, "../migrations"),
    join(cwd, "academy/api/migrations"),
  ];
  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }
  throw new Error(
    `could not locate migrations directory; looked in: ${candidates.join(", ")}`,
  );
}
