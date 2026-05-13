/**
 * Minimal interactive helpers. Avoid `readline.createInterface` lifecycle
 * footguns by wrapping each call in a fresh interface.
 */
import { createInterface } from "node:readline";

export async function ask(question: string, def?: string): Promise<string> {
  const rl = createInterface({ input: process.stdin, output: process.stdout, terminal: true });
  const suffix = def ? ` [${def}]` : "";
  return new Promise<string>((resolve) => {
    rl.question(`${question}${suffix}: `, (raw) => {
      rl.close();
      const v = raw.trim();
      resolve(v || (def ?? ""));
    });
  });
}

export async function confirm(question: string, def = false): Promise<boolean> {
  const hint = def ? "Y/n" : "y/N";
  const r = (await ask(`${question} (${hint})`, def ? "y" : "n")).toLowerCase();
  if (!r) return def;
  return r === "y" || r === "yes";
}
