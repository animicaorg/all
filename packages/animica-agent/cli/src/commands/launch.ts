/**
 * `animica-agent` (no subcommand) launcher.
 *
 * Resolves the best surface for the user:
 *   1. If --no-browser is set OR stdin is a TTY only (no DISPLAY/$BROWSER), fall back to TTY chat.
 *   2. Otherwise start the local UI bridge and try to open the browser.
 *      If `open` fails (no display, --no-browser, etc.), print the URL and
 *      stay running so the user can open it manually.
 *
 * Designed to be the one entry point an end-user runs after `animica-agent setup`.
 */

import { spawn } from "node:child_process";

import { startBridge } from "@animica/agent-ui";

import { boolFlag, stringFlag } from "../args.js";
import { c, header, info, ok, warn } from "../output.js";

const dim = c.dim;
import { runChat } from "./code.js";

/** True when a usable graphical environment is detectable. */
export function detectBrowserSupport(): boolean {
  if (process.platform === "darwin") return true;
  if (process.platform === "win32") return true;
  // Linux/BSD: require either a display server or an explicit $BROWSER.
  if (process.env.DISPLAY) return true;
  if (process.env.WAYLAND_DISPLAY) return true;
  if (process.env.BROWSER) return true;
  return false;
}

/** Best-effort browser opener. Returns true if the launch was attempted. */
export function openBrowser(url: string): boolean {
  let cmd: string;
  let args: string[];
  if (process.platform === "darwin") {
    cmd = "open";
    args = [url];
  } else if (process.platform === "win32") {
    // `start` is a shell builtin; use cmd.exe directly.
    cmd = "cmd";
    args = ["/c", "start", "", url];
  } else {
    // Linux & BSD: prefer $BROWSER if set, fall back to xdg-open.
    if (process.env.BROWSER) {
      cmd = process.env.BROWSER;
      args = [url];
    } else {
      cmd = "xdg-open";
      args = [url];
    }
  }
  try {
    const child = spawn(cmd, args, { stdio: "ignore", detached: true });
    child.on("error", () => {
      /* swallow; caller falls back to manual instructions */
    });
    child.unref();
    return true;
  } catch {
    return false;
  }
}

export async function runLaunch(options: Record<string, string | boolean>): Promise<number> {
  const forceChat = boolFlag(options, "chat", false) || boolFlag(options, "tty", false);
  const noBrowser = boolFlag(options, "no-browser", false);
  if (forceChat) {
    info(c.dim("Starting TTY chat (--chat flag set)."));
    return runChat(options);
  }
  // Headless environments (no DISPLAY / no $BROWSER) → fall back to chat.
  if (!detectBrowserSupport() && !noBrowser) {
    warn("No graphical environment detected; starting TTY chat.");
    info(c.dim("Run `animica-agent ui` to start the dashboard manually."));
    return runChat(options);
  }
  const port = Number.parseInt((stringFlag(options, "port") ?? "4720") as string, 10) || 4720;
  const host = (stringFlag(options, "host") ?? "127.0.0.1") as string;
  let bridge: { url: string; close(): void };
  try {
    bridge = startBridge({ port, host });
  } catch (err) {
    warn(`UI bridge failed to start (${(err as Error).message}); falling back to TTY chat.`);
    return runChat(options);
  }
  header("Animica Coding Agent");
  ok(`local UI ready at ${c.cyan(bridge.url)}`);
  if (!noBrowser) {
    const attempted = openBrowser(bridge.url);
    if (attempted) info(dim("(launched in your default browser)"));
    else info(dim("(could not auto-open; copy the URL above)"));
  }
  info(dim("Ctrl-C to stop the dashboard."));
  await new Promise<void>(() => {
    process.on("SIGINT", () => {
      bridge.close();
      process.exit(0);
    });
  });
  return 0;
}

/** `animica-agent open` — assumes a bridge is already running; tries to open it. */
export async function runOpen(options: Record<string, string | boolean>): Promise<number> {
  const port = Number.parseInt((stringFlag(options, "port") ?? "4720") as string, 10) || 4720;
  const host = (stringFlag(options, "host") ?? "127.0.0.1") as string;
  const url = `http://${host}:${port}/`;
  // Probe the bridge first so we don't open a tab to nothing.
  try {
    const probe = await fetch(`${url}api/health`).catch(() => null);
    if (!probe || !probe.ok) {
      warn(`No UI bridge reachable at ${url}. Start one with \`animica-agent ui\` or \`animica-agent\`.`);
      return 1;
    }
  } catch {
    warn(`UI bridge unreachable at ${url}.`);
    return 1;
  }
  if (!detectBrowserSupport()) {
    info(`Open this URL in a browser: ${c.cyan(url)}`);
    return 0;
  }
  openBrowser(url);
  info(`Opened ${c.cyan(url)} in your default browser.`);
  return 0;
}
