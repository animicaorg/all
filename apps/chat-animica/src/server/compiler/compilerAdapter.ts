import { spawn } from "node:child_process";

export async function compile(source: string) {
  return new Promise<{ bytecode: string; warnings?: string[]; [key: string]: unknown }>((resolve, reject) => {
    const child = spawn("animica-compiler", ["--stdin", "--format", "json"]);
    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (chunk) => { stdout += String(chunk); });
    child.stderr.on("data", (chunk) => { stderr += String(chunk); });
    child.on("error", (error) => reject(new Error(`Compile failed: ${error.message}. Install animica-compiler in PATH.`)));
    child.on("close", (code) => {
      if (code !== 0) return reject(new Error(`Compiler exited with ${code}: ${stderr || "unknown"}`));
      try {
        const parsed = JSON.parse(stdout);
        if (!parsed?.bytecode) throw new Error("Compiler output missing bytecode");
        resolve(parsed);
      } catch (error: any) {
        reject(new Error(`Compiler JSON parse failed: ${error.message}`));
      }
    });

    child.stdin.write(source);
    child.stdin.end();
  });
}
