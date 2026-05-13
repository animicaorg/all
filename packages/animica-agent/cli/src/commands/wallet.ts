import { spawnSync } from "node:child_process";

import {
  fetchBalance,
  isLikelyAnimicaAddress,
  loadConfig,
  probeNode,
  resolveWalletIdentity,
  safeStringify,
  writeProjectConfig,
} from "@animica/agent-core";

import { boolFlag, stringFlag } from "../args.js";
import { c, fail, header, info, kv, ok, warn } from "../output.js";

/** Invoke the bundled Python CLI `animica wallet ...` subcommand. */
function runPythonWallet(args: string[]): { status: number; stdout: string; stderr: string } {
  const python = process.env.ANIMICA_AGENT_PYTHON ?? "python3";
  const r = spawnSync(python, ["-m", "animica.cli.main", "wallet", ...args], { encoding: "utf8" });
  return { status: r.status ?? 1, stdout: r.stdout ?? "", stderr: r.stderr ?? "" };
}

/** Best-effort parse of an address out of typical Python CLI output. */
function extractAddress(text: string): string | undefined {
  const m1 = text.match(/(anm1[0-9ac-hj-np-z]{8,})/i);
  if (m1) return m1[1].toLowerCase();
  const m2 = text.match(/address["'\s:=]+([a-zA-Z0-9]+)/);
  if (m2 && isLikelyAnimicaAddress(m2[1])) return m2[1];
  return undefined;
}

export async function runWalletConnect(positionals: string[], options: Record<string, string | boolean>): Promise<number> {
  const { config, paths } = loadConfig();
  const provided = stringFlag(options, "address") ?? positionals[0];
  if (provided && !isLikelyAnimicaAddress(provided)) {
    fail(`Address does not look like a valid Animica address: ${provided}`);
    return 64;
  }
  const next = provided ? { minerAddress: provided, walletMode: "readonly" as const } : {};
  if (provided) {
    writeProjectConfig(paths, next);
    ok(`saved wallet address ${provided} to ${paths.projectFile}`);
  }
  const merged = { ...config, ...next };
  const wallet = resolveWalletIdentity(merged);
  const node = await probeNode(merged.rpcUrl).catch(() => null);
  header("Wallet");
  kv([
    ["mode", merged.walletMode],
    ["address", wallet?.address],
    ["source", wallet?.source],
    ["rpcReachable", !!node?.reachable],
  ]);
  if (wallet?.address && node?.reachable) {
    const b = await fetchBalance(merged.rpcUrl, wallet.address).catch(() => null);
    kv([
      ["balance", b ? `${b.formattedANM} ANM` : "—"],
      ["balanceRaw", b?.raw.toString() ?? "—"],
    ]);
  } else {
    info(c.dim("Tip: run `animica-agent doctor` to inspect node + wallet health."));
  }
  return 0;
}

export async function runWalletCreate(
  positionals: string[],
  options: Record<string, string | boolean>,
): Promise<number> {
  const label = positionals[0] ?? stringFlag(options, "label") ?? "main";
  const { paths } = loadConfig();
  // Refuse to overwrite an existing label without --force.
  const list = runPythonWallet(["list"]);
  if (list.status === 0 && list.stdout.includes(label) && !boolFlag(options, "force", false)) {
    info(`wallet label "${label}" already exists. Pass --force to recreate, or use a different label.`);
    info(c.dim(list.stdout.trim()));
    return 1;
  }
  const create = runPythonWallet(["new", "--label", label]);
  const combined = create.stdout + "\n" + create.stderr;
  if (create.status !== 0) {
    fail(`wallet create failed (exit ${create.status}). Bundled Python CLI required.`);
    if (boolFlag(options, "verbose", false)) info(combined);
    info(c.dim("Install: pip install animica   (or set ANIMICA_AGENT_PYTHON to your interpreter)"));
    return 1;
  }
  const address = extractAddress(combined);
  // Persist the label and (when found) the address so subsequent commands work.
  const patch: Record<string, string> = { walletLabel: label };
  if (address) patch.minerAddress = address;
  writeProjectConfig(paths, patch as never);
  ok(`created wallet label "${label}"`);
  if (address) {
    kv([
      ["label", label],
      ["address", address],
    ]);
  } else {
    info(c.dim("Could not parse address from CLI output; run `animica-agent wallet address` to inspect."));
  }
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify({ label, address }, { indent: 2 }) + "\n");
  }
  return 0;
}

export async function runWalletAddress(
  positionals: string[],
  options: Record<string, string | boolean>,
): Promise<number> {
  const { config } = loadConfig();
  const label = positionals[0] ?? stringFlag(options, "label") ?? (config as { walletLabel?: string }).walletLabel ?? "main";
  const r = runPythonWallet(["address", "--label", label]);
  const combined = r.stdout + "\n" + r.stderr;
  let address: string | undefined;
  if (r.status === 0) address = extractAddress(combined);
  // Fall back to whatever the agent has remembered.
  if (!address && resolveWalletIdentity(config)) {
    address = resolveWalletIdentity(config)!.address;
  }
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify({ label, address }, { indent: 2 }) + "\n");
    return address ? 0 : 1;
  }
  if (!address) {
    fail(`No address found for label "${label}".`);
    info(c.dim("Run `animica-agent wallet create main` to create one."));
    return 1;
  }
  kv([
    ["label", label],
    ["address", address],
  ]);
  return 0;
}

export async function runWalletList(options: Record<string, string | boolean>): Promise<number> {
  const r = runPythonWallet(["list"]);
  if (r.status !== 0) {
    fail(`wallet list failed (exit ${r.status})`);
    if (boolFlag(options, "verbose", false)) info(r.stderr);
    return 1;
  }
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify({ raw: r.stdout.trim() }, { indent: 2 }) + "\n");
    return 0;
  }
  process.stdout.write(r.stdout);
  return 0;
}

export async function runWalletFundHelp(
  positionals: string[],
  options: Record<string, string | boolean>,
): Promise<number> {
  const { config } = loadConfig();
  const label = positionals[0] ?? stringFlag(options, "label") ?? (config as { walletLabel?: string }).walletLabel ?? "main";
  // Resolve address from the Python CLI, falling back to the agent's view.
  const r = runPythonWallet(["address", "--label", label]);
  const address = (r.status === 0 ? extractAddress(r.stdout + "\n" + r.stderr) : undefined) ?? resolveWalletIdentity(config)?.address;
  const node = await probeNode(config.rpcUrl).catch(() => null);
  let balance: Awaited<ReturnType<typeof fetchBalance>> | null = null;
  if (address && node?.reachable) {
    balance = await fetchBalance(config.rpcUrl, address).catch(() => null);
  }
  if (boolFlag(options, "json", false)) {
    process.stdout.write(
      safeStringify(
        {
          label,
          address,
          rpcUrl: config.rpcUrl,
          chainId: config.chainId,
          balanceRaw: balance?.raw,
          balanceFormatted: balance?.formattedANM,
          funded: !!balance && balance.raw > 0n,
        },
        { indent: 2 },
      ) + "\n",
    );
    return 0;
  }
  header("Fund your Animica wallet");
  if (!address) {
    fail(`No wallet address resolved for label "${label}". Run \`animica-agent wallet create ${label}\` first.`);
    return 1;
  }
  kv([
    ["label", label],
    ["address", address],
    ["chainId", config.chainId],
    ["rpcUrl", config.rpcUrl],
    ["balance", balance ? `${balance.formattedANM} ANM` : "—"],
    ["funded", balance ? (balance.raw > 0n ? "yes" : "no") : "unknown"],
  ]);
  if (balance && balance.raw > 0n) {
    ok("wallet is funded; you can run paid actions.");
    return 0;
  }
  info("");
  info(c.bold("How to fund:"));
  info(`  1. Copy your address: ${c.cyan(address)}`);
  info("  2. Send ANM to that address from any funded wallet, exchange withdrawal,");
  info("     or use a testnet faucet if you're on a test chain.");
  info("  3. Recommended first-use amount: 1 ANM (covers many paid actions; nothing is auto-spent).");
  info("  4. Verify with `animica-agent balance` or refresh the dashboard.");
  info("");
  info(c.dim("Live spending always requires the explicit --i-understand-this-spends-real-funds flag."));
  if (!node?.reachable) {
    warn(`Could not reach the RPC at ${config.rpcUrl}. Run \`animica-node start\` (or set rpcUrl).`);
  }
  return 0;
}

export async function runBalance(positionals: string[], options: Record<string, string | boolean>): Promise<number> {
  const { config } = loadConfig();
  const target = stringFlag(options, "address") ?? positionals[0] ?? resolveWalletIdentity(config)?.address;
  if (!target) {
    fail("no address configured. Pass --address <addr> or run `animica-agent wallet connect <addr>`.");
    return 64;
  }
  const b = await fetchBalance(config.rpcUrl, target);
  kv([
    ["address", target],
    ["reachable", b.reachable],
    ["balance", `${b.formattedANM} ANM`],
    ["raw", b.decimal],
    ["error", b.error ?? null],
  ]);
  return b.reachable ? 0 : 1;
}
