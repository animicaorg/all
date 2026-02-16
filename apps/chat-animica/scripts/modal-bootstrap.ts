#!/usr/bin/env tsx
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

const root = process.cwd();
const envPath = path.join(root, ".env");
const endpointPath = path.join(root, ".modal-endpoint");
const venvPath = path.join(root, ".venv-modal");
const modalDir = path.join(root, "modal");
const isWin = process.platform === "win32";

function parseDotEnv(filePath: string) {
  const result: Record<string, string> = {};
  if (!fs.existsSync(filePath)) return result;
  for (const line of fs.readFileSync(filePath, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const idx = trimmed.indexOf("=");
    if (idx < 0) continue;
    const key = trimmed.slice(0, idx).trim();
    let value = trimmed.slice(idx + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    result[key] = value;
  }
  return result;
}

function run(cmd: string, args: string[], opts?: { cwd?: string; env?: NodeJS.ProcessEnv; allowFail?: boolean }) {
  console.log(`[modal-bootstrap] $ ${cmd} ${args.join(" ")}`);
  const res = spawnSync(cmd, args, {
    cwd: opts?.cwd ?? root,
    env: opts?.env ?? process.env,
    stdio: "pipe",
    encoding: "utf8"
  });
  if (res.stdout) process.stdout.write(res.stdout);
  if (res.stderr) process.stderr.write(res.stderr);
  if (res.status !== 0 && !opts?.allowFail) {
    throw new Error(`Command failed: ${cmd} ${args.join(" ")}`);
  }
  return res;
}

function getPythonBinary(): string | undefined {
  const candidates = ["python3", "python"];
  for (const bin of candidates) {
    const check = spawnSync(bin, ["--version"], { stdio: "ignore" });
    if (check.status === 0) return bin;
  }
  return undefined;
}

function venvPython() {
  return isWin
    ? path.join(venvPath, "Scripts", "python.exe")
    : path.join(venvPath, "bin", "python");
}

function ensureVenv(py: string) {
  if (!fs.existsSync(venvPython())) {
    console.log("[modal-bootstrap] Creating Modal virtualenv at .venv-modal");
    run(py, ["-m", "venv", venvPath]);
  }
}

function installDeps(py: string) {
  run(py, ["-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"]);
  run(py, ["-m", "pip", "install", "modal==0.73.85", "-r", path.join(modalDir, "requirements.txt")]);
}

function parseEndpoint(output: string): string | undefined {
  const patterns = [
    /https:\/\/[-a-zA-Z0-9_.]+\.modal\.run[^\s]*/g,
    /https:\/\/[-a-zA-Z0-9_.]+\.modal\.host[^\s]*/g
  ];
  for (const p of patterns) {
    const matches = output.match(p);
    if (matches?.length) return matches[matches.length - 1];
  }
  return undefined;
}

function validHttps(url?: string) {
  if (!url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:";
  } catch {
    return false;
  }
}

function writeEndpoint(url: string) {
  fs.writeFileSync(endpointPath, `${url}\n`, "utf8");
  console.log(`[modal-bootstrap] Saved endpoint to ${endpointPath}`);
}

function main() {
  const args = new Set(process.argv.slice(2));
  const force = args.has("--force");
  const logs = args.has("--logs");
  const envFile = parseDotEnv(envPath);
  const mergedEnv: NodeJS.ProcessEnv = { ...process.env, ...envFile };

  const endpointFromEnv = (mergedEnv.MODAL_ENDPOINT_URL || "").trim();
  if (!force && validHttps(endpointFromEnv)) {
    console.log("[modal-bootstrap] MODAL_ENDPOINT_URL already set; skipping deploy.");
    return;
  }

  if (!force && fs.existsSync(endpointPath) && validHttps(fs.readFileSync(endpointPath, "utf8").trim())) {
    console.log("[modal-bootstrap] Found .modal-endpoint; skipping deploy.");
    return;
  }

  if (!mergedEnv.MODAL_TOKEN_ID || !mergedEnv.MODAL_TOKEN_SECRET) {
    console.warn("[modal-bootstrap] Modal credentials are missing (MODAL_TOKEN_ID/MODAL_TOKEN_SECRET). Running with local fallback.");
    return;
  }

  const py = getPythonBinary();
  if (!py) {
    console.error("[modal-bootstrap] Python 3 is required. Install Python and rerun `pnpm dev`.");
    return;
  }

  ensureVenv(py);
  const vp = venvPython();

  try {
    installDeps(vp);
  } catch (error) {
    console.error("[modal-bootstrap] Failed to install Modal dependencies.", error);
    return;
  }

  const modalEnv: NodeJS.ProcessEnv = {
    ...mergedEnv,
    MODAL_TOKEN_ID: mergedEnv.MODAL_TOKEN_ID,
    MODAL_TOKEN_SECRET: mergedEnv.MODAL_TOKEN_SECRET,
    MODAL_ENVIRONMENT: mergedEnv.MODAL_ENV || "dev",
    MODAL_REGION: mergedEnv.MODAL_REGION || undefined,
    MODAL_APP_NAME: mergedEnv.MODAL_APP_NAME || "chat-animica-llm"
  };

  if (logs) {
    run(vp, ["-m", "modal", "app", "logs", path.join("modal", "modal_app.py")], { env: modalEnv, allowFail: true });
    return;
  }

  console.log("[modal-bootstrap] Deploying Modal ASGI app...");
  const deploy = run(vp, ["-m", "modal", "deploy", "modal/modal_app.py"], { env: modalEnv, allowFail: true });
  const combined = `${deploy.stdout ?? ""}\n${deploy.stderr ?? ""}`;
  const endpoint = parseEndpoint(combined);

  if (deploy.status !== 0) {
    console.error("[modal-bootstrap] Deploy failed. Check credentials/network and rerun `pnpm --filter chat-animica modal:deploy`.");
    if (endpoint) {
      writeEndpoint(endpoint);
      console.log("[modal-bootstrap] Endpoint was still discovered and saved.");
    }
    return;
  }

  if (endpoint && validHttps(endpoint)) {
    writeEndpoint(endpoint);
    console.log(`[modal-bootstrap] Modal endpoint ready: ${endpoint}`);
    if (!endpointFromEnv) {
      console.log(`[modal-bootstrap] Optional: add MODAL_ENDPOINT_URL=${endpoint} to .env to pin endpoint.`);
    }
    return;
  }

  console.warn("[modal-bootstrap] Deploy succeeded but endpoint URL was not detected. Run `pnpm --filter chat-animica modal:deploy -- --force` to retry and inspect output.");
}

main();
