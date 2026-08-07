// Animica Python Cloud — the host side of the execution sandbox (§26).
//
// Threat model: the Python we are about to run is HOSTILE. It may try to read server secrets,
// reach the wallet, mine crypto, fork-bomb, exhaust RAM, escape the container, or lie to us.
//
// Defenses, in layers:
//   1. OS ISOLATION (primary). One `docker run` per execution with --network none (no egress
//      at all, so exfiltration and pip-install are impossible by construction), --read-only
//      rootfs, --cap-drop ALL, --security-opt no-new-privileges, a non-root uid, cgroup memory
//      and CPU caps, a pid cap, and a small noexec/nosuid tmpfs for /tmp. The Docker socket,
//      host filesystem, env and credentials are never mounted.
//   2. MEDIATED CAPABILITIES. Because the sandbox has no network, EVERY privileged operation
//      (AI, chain reads, paying ANM, calling another function, outbound HTTP) is an RPC to
//      this broker over the runner's private stdio channel. The broker is the only thing that
//      holds credentials, and it authorizes, budgets and meters each call.
//   3. PROTOCOL HARDENING (explicitly NOT a security boundary). The runner moves the protocol
//      onto private descriptors before importing user code and frames carry a per-execution
//      token, so ordinary prints cannot inject frames. But user code runs IN the runner's
//      process, so determined code can still reach those descriptors and emit well-formed
//      frames. That is acceptable and by design: forging a frame buys nothing, because the
//      host authorizes every operation from SERVER-HELD context (the deployment's declared
//      capabilities, the caller's grant, the remaining budget) and never trusts a value the
//      guest sent. Forging a RESULT is equivalent to returning that value; forging a CALL is
//      equivalent to making that call, which is then authorized normally. Notably, billing does
//      NOT trust guest-reported usage — we bill on host-measured wall time.
//   4. HARD DEADLINES. A wall-clock timer kills the container; we never rely on the guest
//      honoring its own timeout.
//
// This module deliberately contains no product logic. The executor injects the capability
// handlers, which keeps this file independently testable and keeps app/business code out of
// the process that talks to hostile input.

import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomBytes, createHash } from 'node:crypto';
import { mkdtemp, writeFile, rm, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { limits, runtime } from './config';

export interface SandboxSpec {
  requestId: string;
  source: string;
  entrypoint: string;
  request: unknown;
  meta: Record<string, unknown>;
  timeoutMs: number;
  memoryMb: number;
  /** Injected as ANM_SECRET_<NAME>. Never logged, never returned. */
  secrets?: Record<string, string>;
}

export interface HostCall {
  op: string;
  args: Record<string, any>;
}

export interface HostReply {
  ok: boolean;
  result?: unknown;
  error?: string;
  code?: string;
}

export type HostHandler = (call: HostCall) => Promise<HostReply>;

export interface SandboxLog {
  level: string;
  message: string;
  ts: number;
}

export interface SandboxResult {
  status: 'ok' | 'error' | 'timeout' | 'crashed';
  result?: unknown;
  error?: string;
  errorType?: string;
  traceback?: string;
  stdout: string;
  logs: SandboxLog[];
  /** Host-measured wall time of the container. This is the resource Animica actually reserves
   *  and the number we bill on — it cannot be under-reported by the guest. */
  wallMs: number;
  /** Guest-reported CPU time. Observability only: a hostile guest could understate it, so it
   *  never drives billing. */
  reportedCpuMs: number;
  maxRssKb: number;
  exitCode: number | null;
  oomKilled: boolean;
}

const FRAME_RE = /^@@ANM:([A-Z]+):([a-f0-9]*)@@ (.*)$/;

/** Cap concurrent containers so the mainnet node on this box is never starved (§25). */
class Semaphore {
  private active = 0;
  private queue: Array<() => void> = [];
  constructor(private max: number) {}
  get inFlight() {
    return this.active;
  }
  get waiting() {
    return this.queue.length;
  }
  async acquire(): Promise<() => void> {
    if (this.active < this.max) {
      this.active++;
      return () => this.release();
    }
    await new Promise<void>((resolve) => this.queue.push(resolve));
    this.active++;
    return () => this.release();
  }
  private release() {
    this.active--;
    const next = this.queue.shift();
    if (next) next();
  }
}

const slots = new Semaphore(limits.globalConcurrency);

export function sandboxLoad() {
  return { inFlight: slots.inFlight, waiting: slots.waiting, capacity: limits.globalConcurrency };
}

export class SandboxBusyError extends Error {
  code = 'sandbox_busy';
}

function clampInt(v: number, lo: number, hi: number, dflt: number): number {
  const n = Number.isFinite(v) ? Math.trunc(v) : dflt;
  return Math.min(hi, Math.max(lo, n));
}

/** Secret names must be plain env-safe identifiers — never let one smuggle shell/env syntax. */
function safeSecretName(name: string): boolean {
  return /^[A-Z][A-Z0-9_]{0,63}$/.test(name);
}

export function sourceSha3(source: string): string {
  // The chain uses SHA3-256 throughout; match it so an artifact hash means the same thing here
  // and in the on-chain anchor.
  return createHash('sha3-256').update(source, 'utf8').digest('hex');
}

/**
 * Run one execution to completion.
 *
 * `onHostCall` is invoked for every capability request from the guest. It MUST enforce
 * permissions and budgets — this function performs no authorization of its own beyond
 * refusing unknown ops.
 */
export async function runSandbox(
  spec: SandboxSpec,
  onHostCall: HostHandler,
  opts: { queueTimeoutMs?: number } = {},
): Promise<SandboxResult> {
  const queueTimeoutMs = opts.queueTimeoutMs ?? 20_000;

  // Admission control: fail fast rather than pile up unbounded work.
  if (slots.waiting >= limits.queueMaxDepth) throw new SandboxBusyError('execution queue is full');
  const acquired = await Promise.race([
    slots.acquire(),
    new Promise<null>((resolve) => setTimeout(() => resolve(null), queueTimeoutMs)),
  ]);
  if (!acquired) throw new SandboxBusyError('timed out waiting for an execution slot');
  const release = acquired;

  const timeoutMs = clampInt(spec.timeoutMs, limits.minTimeoutMs, limits.maxTimeoutMs, limits.defaultTimeoutMs);
  const memoryMb = clampInt(spec.memoryMb, limits.minMemoryMb, limits.maxMemoryMb, limits.defaultMemoryMb);
  const token = randomBytes(16).toString('hex');
  const containerName = `anm-pyc-${spec.requestId.replace(/[^a-zA-Z0-9]/g, '').slice(0, 24)}-${randomBytes(4).toString('hex')}`;

  let workDir: string | null = null;
  let child: ChildProcessWithoutNullStreams | null = null;
  const started = Date.now();
  const logs: SandboxLog[] = [];

  try {
    // Stage the code on disk; it is bind-mounted READ-ONLY into the container.
    workDir = await mkdtemp(path.join(tmpdir(), 'anm-pyc-'));
    const codeDir = path.join(workDir, 'code');
    await mkdir(codeDir, { recursive: true });
    await writeFile(path.join(codeDir, 'handler.py'), spec.source, { mode: 0o444 });

    const env: string[] = ['-e', `ANM_PROTO_TOKEN=${token}`, '-e', 'ANM_CAPTURE_PATH=/tmp/.anm-stdout'];
    for (const [k, v] of Object.entries(spec.secrets ?? {})) {
      if (!safeSecretName(k)) continue;
      env.push('-e', `ANM_SECRET_${k}=${v}`);
    }

    const args = [
      'run',
      '--rm',
      '-i',
      '--name',
      containerName,
      // No network at all: no exfiltration, no crypto mining pool, no pip, no SSRF.
      '--network',
      'none',
      '--read-only',
      '--cap-drop',
      'ALL',
      '--security-opt',
      'no-new-privileges',
      '--user',
      `${runtime.uid}:${runtime.gid}`,
      '--memory',
      `${memoryMb}m`,
      // Equal swap limit => the memory cap is a real cap, not a swap invitation. This box has
      // no swap at all, so an unbounded container would trigger the host OOM killer.
      '--memory-swap',
      `${memoryMb}m`,
      '--cpus',
      String(Number(process.env.CLOUD_SANDBOX_CPUS ?? '1')),
      '--pids-limit',
      String(limits.maxPids),
      '--ulimit',
      'nofile=256:256',
      // NB: deliberately NO `--ulimit nproc`. RLIMIT_NPROC is counted per-UID across the HOST,
      // not per-container, so with a shared sandbox uid it makes `exec` fail with EAGAIN as
      // soon as a few containers run concurrently. The cgroup pids controller (--pids-limit
      // above) is the correct, container-scoped fork-bomb defense.
      '--tmpfs',
      `/tmp:rw,noexec,nosuid,nodev,size=${limits.maxTmpfsMb}m,mode=1777`,
      '-v',
      `${codeDir}:/app/code:ro`,
      '--label',
      'animica.pycloud=1',
      ...env,
      runtime.image,
    ];

    child = spawn(runtime.docker, args, { stdio: ['pipe', 'pipe', 'pipe'] }) as ChildProcessWithoutNullStreams;

    let settled = false;
    let resultFrame: any = null;
    let protoError: string | null = null;
    let stderrBuf = '';
    let timedOut = false;

    const finish = new Promise<void>((resolve) => {
      const done = () => {
        if (!settled) {
          settled = true;
          resolve();
        }
      };
      child!.on('close', done);
      child!.on('error', (e) => {
        protoError = `sandbox spawn failed: ${e.message}`;
        done();
      });
    });

    // Hard kill. We never trust the guest to respect its own deadline: SIGKILL the container,
    // and `docker kill` as a belt in case the client process is wedged.
    const killer = setTimeout(() => {
      timedOut = true;
      try {
        child?.kill('SIGKILL');
      } catch {
        /* already gone */
      }
      spawn(runtime.docker, ['kill', containerName], { stdio: 'ignore' }).on('error', () => {});
    }, timeoutMs + 5_000);

    const writeReply = (obj: unknown) => {
      if (child && !child.killed && child.stdin.writable) {
        child.stdin.write(JSON.stringify(obj) + '\n');
      }
    };

    // BOOT frame.
    writeReply({
      config: {
        code_dir: '/app/code',
        module: 'handler',
        entrypoint: spec.entrypoint || 'main',
        timeout_ms: timeoutMs,
        memory_mb: memoryMb,
        max_pids: limits.maxPids,
        max_output_bytes: limits.maxOutputBytes,
      },
      request: spec.request ?? {},
      meta: spec.meta ?? {},
    });

    // Line-oriented frame reader over the private protocol stream.
    let buf = '';
    const pending: Promise<void>[] = [];
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk: string) => {
      buf += chunk;
      let idx: number;
      while ((idx = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, idx);
        buf = buf.slice(idx + 1);
        const m = FRAME_RE.exec(line);
        if (!m) continue; // not a protocol frame — ignore (guest cannot reach this stream anyway)
        const [, kind, frameToken, body] = m;
        if (frameToken !== token) continue; // forged or stale frame
        if (kind === 'RESULT') {
          try {
            const outer = JSON.parse(body);
            resultFrame = JSON.parse(Buffer.from(outer.b64, 'base64').toString('utf8'));
          } catch (e: any) {
            protoError = `bad result frame: ${e?.message ?? e}`;
          }
        } else if (kind === 'ERROR') {
          try {
            protoError = JSON.parse(body).error ?? 'runner error';
          } catch {
            protoError = 'runner error';
          }
        } else if (kind === 'LOG') {
          try {
            const l = JSON.parse(body);
            if (logs.length < limits.maxLogLines) {
              logs.push({
                level: String(l.level ?? 'info'),
                message: String(l.message ?? '').slice(0, limits.maxLogLineChars),
                ts: Date.now(),
              });
            }
          } catch {
            /* malformed log frame: drop it */
          }
        } else if (kind === 'CALL') {
          let call: any;
          try {
            call = JSON.parse(body);
          } catch {
            continue;
          }
          // Serialize host calls: the guest waits for each reply before issuing the next.
          const p = (async () => {
            let reply: HostReply;
            try {
              reply = await onHostCall({ op: String(call.op ?? ''), args: call.args ?? {} });
            } catch (e: any) {
              reply = { ok: false, code: e?.code ?? 'host_error', error: String(e?.message ?? e).slice(0, 500) };
            }
            writeReply({ id: call.id, ...reply });
          })();
          pending.push(p);
          p.catch(() => {});
        }
      }
      if (buf.length > 4 * 1024 * 1024) buf = ''; // never let a chatty guest grow our heap
    });

    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (c: string) => {
      if (stderrBuf.length < 8192) stderrBuf += c;
    });

    await finish;
    clearTimeout(killer);
    await Promise.allSettled(pending);

    const wallMs = Date.now() - started;
    const exitCode = child.exitCode;
    // 137 = SIGKILL, which on this box means either our timeout or the cgroup OOM killer.
    const oomKilled = exitCode === 137 && !timedOut;

    if (timedOut) {
      return {
        status: 'timeout',
        error: `execution exceeded its ${timeoutMs}ms budget`,
        stdout: resultFrame?.stdout ?? '',
        logs,
        wallMs,
        reportedCpuMs: resultFrame?.usage?.cpu_ms ?? 0,
        maxRssKb: resultFrame?.usage?.max_rss_kb ?? 0,
        exitCode,
        oomKilled: false,
      };
    }

    if (!resultFrame) {
      const detail = protoError || stderrBuf.trim().slice(-500) || `exit ${exitCode}`;
      return {
        status: 'crashed',
        error: oomKilled ? `execution exceeded its ${memoryMb}MB memory limit` : `sandbox produced no result (${detail})`,
        stdout: '',
        logs,
        wallMs,
        reportedCpuMs: 0,
        maxRssKb: 0,
        exitCode,
        oomKilled,
      };
    }

    const status = resultFrame.status === 'ok' ? 'ok' : resultFrame.status === 'timeout' ? 'timeout' : 'error';
    return {
      status,
      result: resultFrame.result,
      error: resultFrame.error,
      errorType: resultFrame.type,
      traceback: resultFrame.traceback,
      stdout: String(resultFrame.stdout ?? '').slice(0, limits.maxOutputBytes),
      logs,
      wallMs,
      reportedCpuMs: Number(resultFrame.usage?.cpu_ms ?? 0),
      maxRssKb: Number(resultFrame.usage?.max_rss_kb ?? 0),
      exitCode,
      oomKilled,
    };
  } finally {
    release();
    if (workDir) await rm(workDir, { recursive: true, force: true }).catch(() => {});
  }
}

/**
 * Reap orphaned sandbox containers.
 *
 * studio-host's containers were observed "Up 6 weeks" despite a 20-minute idle TTL, which is
 * exactly the failure mode to avoid: in-memory tracking dies with the process. This sweeps by
 * label so a restarted app still cleans up its predecessor's strays.
 */
export async function reapOrphans(maxAgeSeconds = 900): Promise<number> {
  const list = await new Promise<string>((resolve) => {
    const p = spawn(runtime.docker, ['ps', '-q', '--filter', 'label=animica.pycloud=1']);
    let out = '';
    p.stdout.on('data', (d) => (out += d.toString()));
    p.on('close', () => resolve(out));
    p.on('error', () => resolve(''));
  });
  const ids = list.split('\n').map((s) => s.trim()).filter(Boolean);
  let killed = 0;
  for (const id of ids) {
    const started = await new Promise<string>((resolve) => {
      const p = spawn(runtime.docker, ['inspect', '-f', '{{.State.StartedAt}}', id]);
      let out = '';
      p.stdout.on('data', (d) => (out += d.toString()));
      p.on('close', () => resolve(out.trim()));
      p.on('error', () => resolve(''));
    });
    const age = started ? (Date.now() - Date.parse(started)) / 1000 : Infinity;
    if (age > maxAgeSeconds) {
      await new Promise<void>((resolve) => {
        const p = spawn(runtime.docker, ['kill', id], { stdio: 'ignore' });
        p.on('close', () => resolve());
        p.on('error', () => resolve());
      });
      killed++;
    }
  }
  return killed;
}
