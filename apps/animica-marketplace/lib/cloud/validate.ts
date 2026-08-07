// Animica Python Cloud — host side of pre-deploy static validation (§4, §40).
//
// The actual analysis lives in sandbox/validate.py (AST-only, never executes the code) so the
// rules stay next to the runner they protect and can be unit-tested in Python. This module is
// the process boundary: it feeds the validator {source, entrypoint, max_bytes} on stdin and
// reads one JSON report from stdout.
//
// FAIL CLOSED. A validator that crashes, times out, or emits garbage means we know NOTHING
// about the code — and unvetted code must never proceed toward deployment. Every abnormal exit
// becomes a hard `ok:false` report with a `validator_unavailable`-class error finding, so the
// pipeline records an honest reason instead of silently skipping the check.

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { limits } from './config';

export interface ValidationFinding {
  severity: 'error' | 'warning';
  code: string;
  message: string;
  line: number;
  col: number;
}

export interface ValidationReport {
  ok: boolean;
  findings: ValidationFinding[];
  /** Module-level function names discovered by the AST pass. */
  functions: string[];
  /** Root module names the source imports. */
  imports: string[];
  /** Capabilities the source appears to use (animica.ai/... call-site inference) — advisory,
   *  used to pre-fill the declared-capabilities UI. Enforcement is the host broker's job. */
  capabilities: string[];
}

// 5s wall clock. ast.parse on a 256KB source is milliseconds; anything that takes seconds is a
// pathological input (deeply nested parens is a known parser DoS) and gets refused, not waited on.
const VALIDATOR_TIMEOUT_MS = 5_000;

// The venv interpreter matches what the node/CLI stack runs; a bare python3 works too because
// the validator is stdlib-only (ast/json/sys). Env-overridable like every other runtime knob.
function pythonBin(): string {
  const fromEnv = process.env.CLOUD_PYTHON_BIN;
  if (fromEnv) return fromEnv;
  const venv = '/root/animica/.venv/bin/python3';
  return existsSync(venv) ? venv : 'python3';
}

// The Next.js server and the workers both run with cwd at the app root, so the relative
// resolution works everywhere; the absolute fallback covers a caller with a different cwd.
function validatorPath(): string {
  const fromEnv = process.env.CLOUD_VALIDATOR_PATH;
  if (fromEnv) return fromEnv;
  const local = path.join(process.cwd(), 'sandbox', 'validate.py');
  return existsSync(local) ? local : '/root/animica/apps/animica-marketplace/sandbox/validate.py';
}

function hardFailure(code: string, message: string): ValidationReport {
  return {
    ok: false,
    findings: [{ severity: 'error', code, message, line: 0, col: 0 }],
    functions: [],
    imports: [],
    capabilities: [],
  };
}

/** True when a report failed because the VALIDATOR was broken, not because the code was bad.
 *  Callers surface this as a 503 (retryable platform fault), not a 422 (fix your code). */
export function isValidatorFault(report: ValidationReport): boolean {
  return report.findings.some((f) => f.code === 'validator_crashed' || f.code === 'validator_timeout' || f.code === 'validator_unparseable');
}

function toStringArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x) => typeof x === 'string') : [];
}

function normalizeFindings(v: unknown): ValidationFinding[] {
  if (!Array.isArray(v)) return [];
  const out: ValidationFinding[] = [];
  for (const f of v.slice(0, 200)) {
    if (!f || typeof f !== 'object') continue;
    const sev = (f as any).severity === 'warning' ? 'warning' : 'error'; // unknown severity => error (closed)
    out.push({
      severity: sev,
      code: String((f as any).code ?? 'unknown').slice(0, 64),
      message: String((f as any).message ?? '').slice(0, 500),
      line: Number.isFinite(Number((f as any).line)) ? Math.max(0, Math.trunc(Number((f as any).line))) : 0,
      col: Number.isFinite(Number((f as any).col)) ? Math.max(0, Math.trunc(Number((f as any).col))) : 0,
    });
  }
  return out;
}

/**
 * Run the static validator over one function source.
 *
 * Contract with sandbox/validate.py: stdin JSON {source, entrypoint, max_bytes}; stdout one JSON
 * report {ok, findings:[{severity,code,message,line,col}], functions, imports, capabilities}.
 */
export async function validateSource(input: { source: string; entrypoint: string }): Promise<ValidationReport> {
  const payload = JSON.stringify({
    source: input.source,
    entrypoint: input.entrypoint || 'main',
    max_bytes: limits.maxSourceBytes,
  });

  return new Promise<ValidationReport>((resolve) => {
    let settled = false;
    const done = (r: ValidationReport) => {
      if (!settled) {
        settled = true;
        resolve(r);
      }
    };

    // -I (isolated) so the validator cannot be influenced by PYTHONPATH/sitecustomize on the
    // host — its verdict must depend only on the source under review.
    const child = spawn(pythonBin(), ['-I', validatorPath()], { stdio: ['pipe', 'pipe', 'pipe'] });

    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (c: string) => {
      if (stdout.length < 4 * 1024 * 1024) stdout += c;
    });
    child.stderr.on('data', (c: string) => {
      if (stderr.length < 8192) stderr += c;
    });

    const killer = setTimeout(() => {
      try {
        child.kill('SIGKILL');
      } catch {
        /* already gone */
      }
      done(hardFailure('validator_timeout', `the validator did not finish within ${VALIDATOR_TIMEOUT_MS}ms`));
    }, VALIDATOR_TIMEOUT_MS);

    child.on('error', (e) => {
      clearTimeout(killer);
      done(hardFailure('validator_crashed', `could not start the validator: ${e.message}`));
    });

    child.on('close', (code) => {
      clearTimeout(killer);
      if (code !== 0) {
        done(
          hardFailure(
            'validator_crashed',
            `the validator exited with code ${code}${stderr.trim() ? `: ${stderr.trim().slice(0, 300)}` : ''}`,
          ),
        );
        return;
      }
      let parsed: any;
      try {
        parsed = JSON.parse(stdout);
      } catch {
        done(hardFailure('validator_unparseable', 'the validator produced no parseable report'));
        return;
      }
      if (!parsed || typeof parsed !== 'object') {
        done(hardFailure('validator_unparseable', 'the validator report was not an object'));
        return;
      }
      const findings = normalizeFindings(parsed.findings);
      // Trust the errors, not the flag: ok must be derivable from the findings so a validator
      // bug that sets ok:true alongside error findings still fails closed.
      const hasErrors = findings.some((f) => f.severity === 'error');
      done({
        ok: Boolean(parsed.ok) && !hasErrors,
        findings,
        functions: toStringArray(parsed.functions),
        imports: toStringArray(parsed.imports),
        capabilities: toStringArray(parsed.capabilities),
      });
    });

    child.stdin.write(payload);
    child.stdin.end();
  });
}
