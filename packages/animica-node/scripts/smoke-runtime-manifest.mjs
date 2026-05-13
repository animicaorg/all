#!/usr/bin/env node
/**
 * Smoke-test a runtime manifest URL end to end through runtime-manager.
 *
 * For the host platform this validates download, sha256, extraction,
 * activation, and entry resolution. For other platforms, pass --platform to
 * structurally validate download/extract/entry without native execution.
 */

import { mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join } from "node:path";
import { pathToFileURL } from "node:url";

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  if (i !== -1 && i + 1 < process.argv.length) return process.argv[i + 1];
  return fallback;
}
function flag(name) {
  return process.argv.includes(`--${name}`);
}

const manifestUrl = arg("manifest-url", arg("manifest"));
const platform = arg("platform");
const keep = flag("keep");

if (!manifestUrl) {
  process.stderr.write("error: --manifest-url <url-or-path> is required\n");
  process.exit(64);
}

const runtimeHome = mkdtempSync(join(tmpdir(), "animica-runtime-smoke-"));
const priorHome = process.env.ANIMICA_RUNTIME_HOME;
process.env.ANIMICA_RUNTIME_HOME = runtimeHome;
let server;

try {
  const managerUrl = pathToFileURL(join(process.cwd(), "dist", "runtime-manager.js")).href;
  const { fetchManifest, installRuntime, runtimeStatus } = await import(managerUrl);
  const resolvedManifestUrl = await resolveManifestUrlForSmoke(manifestUrl);
  const manifest = await fetchManifest(resolvedManifestUrl);
  const result = await installRuntime({
    manifest,
    platformKey: platform,
    force: true,
  });
  const status = runtimeStatus({ manifestUrl: resolvedManifestUrl });
  process.stdout.write(
    JSON.stringify(
      {
        ok: true,
        manifest: `${manifest.channel}@${manifest.version}`,
        platform: result.installed.platformKey,
        entry: result.installed.entry,
        installDir: result.installed.installDir,
        activated: result.activated,
        active: status.active,
        note:
          platform && platform !== status.platformKey
            ? "structural validation only; native execution was not tested on this host"
            : "host-platform install path validated",
      },
      null,
      2,
    ) + "\n",
  );
} catch (err) {
  process.stderr.write(`error: ${(err && err.message) || String(err)}\n`);
  process.exitCode = 1;
} finally {
  if (server) await new Promise((resolve) => server.close(resolve));
  if (priorHome === undefined) delete process.env.ANIMICA_RUNTIME_HOME;
  else process.env.ANIMICA_RUNTIME_HOME = priorHome;
  if (!keep) rmSync(runtimeHome, { recursive: true, force: true });
}

async function resolveManifestUrlForSmoke(input) {
  const localPath = input.startsWith("/") || (!/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(input) && existsSync(input))
    ? input
    : null;
  if (!localPath) return input;
  const releaseDir = dirname(localPath);
  const manifest = JSON.parse(readFileSync(localPath, "utf8"));
  server = createServer((req, res) => {
    const url = req.url || "/";
    if (url === "/manifest.json") {
      const port = server.address().port;
      const localManifest = {
        ...manifest,
        assets: Object.fromEntries(
          Object.entries(manifest.assets).map(([key, asset]) => [
            key,
            {
              ...asset,
              url: `http://127.0.0.1:${port}/${basename(new URL(asset.url).pathname)}`,
            },
          ]),
        ),
      };
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify(localManifest));
      return;
    }
    const file = join(releaseDir, basename(url));
    if (!existsSync(file)) {
      res.writeHead(404);
      res.end();
      return;
    }
    const body = readFileSync(file);
    res.writeHead(200, { "content-type": "application/octet-stream", "content-length": String(body.length) });
    res.end(body);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return `http://127.0.0.1:${server.address().port}/manifest.json`;
}
