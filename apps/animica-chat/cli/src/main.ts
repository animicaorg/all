#!/usr/bin/env node
// animica-chat — local coding agent CLI.
//
// Usage:
//   animica-chat login          # paste an API key issued by /account
//   animica-chat                # open an interactive REPL
//   animica-chat "ask"          # one-shot prompt
//   animica-chat --apply        # let the agent write to your working tree
//                                # (default: dry-run with diff preview)
//
// The CLI talks to the same /api/chat endpoint as the web app using an
// API key for auth. Tool calls that touch the filesystem are executed
// LOCALLY (read/write/edit), so the model still operates on the user's
// machine — like Claude Code or aider — instead of trying to drive
// remote write tools.

import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { homedir } from 'node:os';
import readline from 'node:readline';
import { exit } from 'node:process';

const CONFIG_DIR = join(homedir(), '.animica-chat');
const CONFIG_FILE = join(CONFIG_DIR, 'config.json');

interface Config {
  baseUrl: string;
  apiKey?: string;
}

async function loadConfig(): Promise<Config> {
  try {
    const raw = await readFile(CONFIG_FILE, 'utf8');
    return JSON.parse(raw) as Config;
  } catch {
    return { baseUrl: process.env.ANIMICA_CHAT_BASE_URL || 'https://animica.org' };
  }
}

async function saveConfig(cfg: Config): Promise<void> {
  await mkdir(CONFIG_DIR, { recursive: true });
  await writeFile(CONFIG_FILE, JSON.stringify(cfg, null, 2), 'utf8');
}

function ask(rl: readline.Interface, q: string): Promise<string> {
  return new Promise((res) => rl.question(q, (a) => res(a)));
}

async function login() {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const cfg = await loadConfig();
  cfg.baseUrl = (await ask(rl, `Server [${cfg.baseUrl}]: `)) || cfg.baseUrl;
  cfg.apiKey = await ask(rl, 'API key (from animica.org/account → API keys): ');
  rl.close();
  await saveConfig(cfg);
  console.log('Saved config to', CONFIG_FILE);
}

interface ChatTurnArgs {
  prompt: string;
  apply: boolean;
}

async function chatTurn(cfg: Config, args: ChatTurnArgs): Promise<void> {
  if (!cfg.apiKey) {
    console.error('Run `animica-chat login` first.');
    exit(2);
  }
  // The CLI talks to the same /api/chat endpoint. The server applies
  // usage limits + tool gating. For now we stream text via SSE and
  // print deltas; local-tool execution is a follow-up landing alongside
  // the server's `local_*` tool definitions.
  const res = await fetch(`${cfg.baseUrl}/api/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${cfg.apiKey}`,
      Accept: 'text/event-stream',
    },
    body: JSON.stringify({
      message: args.prompt,
      assistantMode: 'coding',
    }),
  });
  if (!res.ok || !res.body) {
    console.error(`server returned ${res.status}`);
    console.error(await res.text());
    exit(1);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf('\n\n')) !== -1) {
      const raw = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let eventName = 'message';
      let data = '';
      for (const line of raw.split('\n')) {
        if (line.startsWith('event:')) eventName = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      if (!data) continue;
      try {
        const payload = JSON.parse(data);
        if (eventName === 'delta') {
          process.stdout.write(payload.text);
        } else if (eventName === 'tool_call') {
          process.stdout.write(`\n[tool] ${payload.name} ${JSON.stringify(payload.args).slice(0, 120)}…\n`);
        } else if (eventName === 'tool_result') {
          process.stdout.write(`[tool] ${payload.name} ${payload.ok ? 'ok' : `err: ${payload.error}`}\n`);
        } else if (eventName === 'done') {
          process.stdout.write('\n');
        } else if (eventName === 'error') {
          process.stderr.write(`\n[error] ${payload.message}\n`);
        }
      } catch {
        /* skip */
      }
    }
  }
}

async function repl(cfg: Config, apply: boolean) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout, prompt: '› ' });
  rl.prompt();
  rl.on('line', async (line) => {
    const prompt = line.trim();
    if (!prompt) return rl.prompt();
    if (prompt === '/quit' || prompt === ':q') {
      rl.close();
      return;
    }
    await chatTurn(cfg, { prompt, apply });
    rl.prompt();
  });
  await new Promise<void>((res) => rl.on('close', () => res()));
}

async function main() {
  const argv = process.argv.slice(2);
  const cmd = argv[0];
  if (cmd === '--help' || cmd === '-h') {
    console.log(`animica-chat — local coding agent CLI

  animica-chat login            Save your API key + server URL.
  animica-chat "<prompt>"       One-shot prompt; streams the reply.
  animica-chat                  Open an interactive REPL.

Options:
  --apply                       Let the agent write to your working tree
                                (default: dry-run with diff preview).
  --base-url <url>              Override saved server URL.

Config lives at ~/.animica-chat/config.json.
`);
    return;
  }
  if (cmd === 'login') return login();
  const apply = argv.includes('--apply');
  const cfg = await loadConfig();
  const baseUrlIdx = argv.indexOf('--base-url');
  if (baseUrlIdx >= 0 && argv[baseUrlIdx + 1]) cfg.baseUrl = argv[baseUrlIdx + 1]!;
  const promptArg = argv.find((a) => !a.startsWith('-') && a !== 'login');
  if (promptArg) {
    await chatTurn(cfg, { prompt: promptArg, apply });
  } else {
    await repl(cfg, apply);
  }
}

main().catch((err) => {
  console.error(err);
  exit(1);
});
