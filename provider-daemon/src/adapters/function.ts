/**
 * Animica Studio `function_compute` adapter.
 *
 * Executes a packed Studio call (produced by python/animica/studio/serialize.py
 * ::pack_call) inside the sandbox-runner service, and returns the worker's
 * packed result envelope.
 *
 * The wire contract is the shared Studio envelope:
 *
 *   call spec (input.call):
 *     ref:    {version,mode:"ref",entrypoint:"<mod>:<qual>",args,kwargs,
 *              source_b64,source_filename}
 *     pickle: {version,mode:"pickle",entrypoint,pickle_b64}
 *
 *   the in-sandbox program prints ONE sentinel line on stdout:
 *     success: __ANIMICA_STUDIO_RESULT__<b64(json({kind,value}))>
 *     failure: __ANIMICA_STUDIO_ERROR__<b64(json({message,traceback}))>
 *
 * The Python program embedded below is a faithful, dependency-free reproduction
 * of python/animica/studio/_wrapper.py — it resolves a ref entrypoint from
 * shipped source (unwrapping a Studio @function via the
 * `__animica_studio_function__` marker) or cloudpickle-loads a pickle call, runs
 * it, and emits the sentinel. The only change vs. _wrapper.py is that the call
 * spec is embedded as a base64 literal instead of read from argv/env, so the
 * program is fully self-contained.
 */

const RESULT_SENTINEL = '__ANIMICA_STUDIO_RESULT__';
const ERROR_SENTINEL = '__ANIMICA_STUDIO_ERROR__';

const SANDBOX_URL = process.env.AICF_SANDBOX_URL ?? 'http://127.0.0.1:8004';
const DEFAULT_TIMEOUT_S = Number(process.env.AICF_FUNCTION_TIMEOUT_S ?? 600);

type StudioResult = { kind: 'json' | 'pickle' | 'repr'; value: unknown };
type StudioError = { message: string; traceback?: string };

type SandboxResponse = {
  stdout: string;
  stderr: string;
  exit_code: number;
  execution_time: number;
};

/**
 * Build the self-contained Python program. `callB64` is base64(json(call spec)).
 * Mirrors _wrapper.py: _module_from_source / _resolve_ref / _pack_result / main.
 */
function buildProgram(callB64: string): string {
  // NOTE: kept ASCII + carefully escaped; the call spec is injected as a base64
  // string literal so no user data is ever interpolated into Python syntax.
  return [
    'from __future__ import annotations',
    'import base64, importlib, importlib.util, json, sys, traceback, types',
    '',
    'RESULT_SENTINEL = "' + RESULT_SENTINEL + '"',
    'ERROR_SENTINEL = "' + ERROR_SENTINEL + '"',
    'CALL_B64 = "' + callB64 + '"',
    '',
    'def _b64e(data):',
    '    return base64.b64encode(data).decode("ascii")',
    '',
    'def _b64d(s):',
    '    return base64.b64decode(s.encode("ascii"))',
    '',
    'def _module_from_source(source, filename):',
    '    name = "__animica_studio_target__"',
    '    mod = types.ModuleType(name)',
    '    mod.__file__ = filename or "<studio-target>"',
    '    sys.modules[name] = mod',
    '    code = compile(source, mod.__file__, "exec")',
    '    exec(code, mod.__dict__)',
    '    return mod',
    '',
    'def _resolve_ref(entrypoint, source_b64, filename):',
    '    module_name, _, qualname = entrypoint.partition(":")',
    '    if source_b64:',
    '        mod = _module_from_source(_b64d(source_b64).decode("utf-8"), filename)',
    '    else:',
    '        mod = importlib.import_module(module_name)',
    '    obj = mod',
    '    for part in qualname.split("."):',
    '        if part == "<locals>":',
    '            raise RuntimeError(',
    '                "cannot resolve closure target %r in ref mode; use serialization=\'pickle\'" % (entrypoint,)',
    '            )',
    '        obj = getattr(obj, part)',
    '    if getattr(obj, "__animica_studio_function__", False):',
    '        obj = getattr(obj, "fn", obj)',
    '    return obj',
    '',
    'def _pack_result(value):',
    '    try:',
    '        json.dumps(value)',
    '        return {"kind": "json", "value": value}',
    '    except (TypeError, ValueError):',
    '        try:',
    '            import cloudpickle',
    '            return {"kind": "pickle", "value": _b64e(cloudpickle.dumps(value))}',
    '        except Exception:',
    '            return {"kind": "repr", "value": repr(value)}',
    '',
    'def main():',
    '    try:',
    '        spec = json.loads(_b64d(CALL_B64).decode("utf-8"))',
    '    except Exception as exc:',
    '        sys.stdout.write(ERROR_SENTINEL + _b64e(json.dumps(',
    '            {"message": "spec load failed: %s" % (exc,), "traceback": traceback.format_exc()}',
    '        ).encode("utf-8")) + "\\n")',
    '        return 2',
    '    try:',
    '        mode = spec.get("mode", "ref")',
    '        if mode == "pickle":',
    '            import cloudpickle',
    '            fn, args, kwargs = cloudpickle.loads(_b64d(spec["pickle_b64"]))',
    '        else:',
    '            fn = _resolve_ref(spec["entrypoint"], spec.get("source_b64"), spec.get("source_filename"))',
    '            args = tuple(spec.get("args", []))',
    '            kwargs = dict(spec.get("kwargs", {}))',
    '        result = fn(*args, **kwargs)',
    '        packed = _pack_result(result)',
    '        sys.stdout.flush()',
    '        sys.stdout.write(RESULT_SENTINEL + _b64e(json.dumps(packed).encode("utf-8")) + "\\n")',
    '        sys.stdout.flush()',
    '        return 0',
    '    except Exception as exc:',
    '        sys.stdout.flush()',
    '        sys.stdout.write(ERROR_SENTINEL + _b64e(json.dumps(',
    '            {"message": str(exc), "traceback": traceback.format_exc()}',
    '        ).encode("utf-8")) + "\\n")',
    '        sys.stdout.flush()',
    '        return 1',
    '',
    'if __name__ == "__main__":',
    '    raise SystemExit(main())',
    ''
  ].join('\n');
}

/** Find and decode the final Studio sentinel line in raw stdout. */
function parseSentinel(stdout: string): {
  studioResult?: StudioResult;
  studioError?: StudioError;
  sentinelLine?: string;
} {
  const lines = stdout.split('\n');
  // Scan from the end: the sentinel is the final meaningful line the worker emits.
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i].trim();
    if (line.startsWith(RESULT_SENTINEL)) {
      const payload = line.slice(RESULT_SENTINEL.length);
      try {
        const decoded = Buffer.from(payload, 'base64').toString('utf-8');
        return { studioResult: JSON.parse(decoded) as StudioResult, sentinelLine: line };
      } catch {
        return { sentinelLine: line };
      }
    }
    if (line.startsWith(ERROR_SENTINEL)) {
      const payload = line.slice(ERROR_SENTINEL.length);
      try {
        const decoded = Buffer.from(payload, 'base64').toString('utf-8');
        return { studioError: JSON.parse(decoded) as StudioError, sentinelLine: line };
      } catch {
        return { sentinelLine: line };
      }
    }
  }
  return {};
}

/**
 * Execute a Studio function_compute job.
 *
 * `input` is the job's `request.input`, i.e. {call, image_ref, image, secrets,
 * volumes, ...}. We use `input.call` (the packed call spec) and may inspect
 * `input.image` for runtime hints. Never throws on a function-level error: the
 * error sentinel is surfaced in `output.studio_error` so the daemon submits the
 * failure faithfully.
 */
export async function runFunctionAdapter(
  input: Record<string, unknown>
): Promise<{
  output: Record<string, unknown>;
  usage: {
    inputTokens: number;
    outputTokens: number;
    bytesIn: number;
    bytesOut: number;
    latencyMs: number;
  };
}> {
  const startedAt = Date.now();
  const inputJson = JSON.stringify(input ?? {});
  const bytesIn = Buffer.byteLength(inputJson, 'utf-8');

  const call = (input?.call ?? null) as Record<string, unknown> | null;
  if (!call || typeof call !== 'object') {
    const latencyMs = Date.now() - startedAt;
    return {
      output: {
        text: '',
        studio_error: { message: 'function_compute job missing input.call spec' },
        runtime: 'function-py'
      },
      usage: { inputTokens: 0, outputTokens: 0, bytesIn, bytesOut: 0, latencyMs }
    };
  }

  // Per-job timeout: prefer the image's declared timeout, else env default.
  const image = (input?.image ?? null) as Record<string, unknown> | null;
  const imageTimeout = image && typeof image.timeout === 'number' ? (image.timeout as number) : undefined;
  const timeoutS = Math.max(1, Math.ceil(imageTimeout ?? DEFAULT_TIMEOUT_S));

  const callB64 = Buffer.from(JSON.stringify(call), 'utf-8').toString('base64');
  const program = buildProgram(callB64);

  const controller = new AbortController();
  // Add a small grace period over the sandbox-side timeout so the runner can
  // return its own 408 rather than us aborting the HTTP request first.
  const abortTimer = setTimeout(() => controller.abort(), (timeoutS + 30) * 1000);

  let sandbox: SandboxResponse;
  try {
    const response = await fetch(`${SANDBOX_URL}/execute`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        language: 'python',
        code: program,
        timeout: timeoutS,
        memory_limit: typeof image?.memory === 'string' ? (image.memory as string) : '512MB'
      }),
      signal: controller.signal
    });

    if (!response.ok) {
      const text = await response.text().catch(() => '');
      const latencyMs = Date.now() - startedAt;
      return {
        output: {
          text: '',
          studio_error: {
            message: `sandbox runner returned ${response.status}: ${text.slice(0, 500)}`
          },
          runtime: 'function-py'
        },
        usage: { inputTokens: 0, outputTokens: 0, bytesIn, bytesOut: 0, latencyMs }
      };
    }

    sandbox = (await response.json()) as SandboxResponse;
  } catch (error) {
    const latencyMs = Date.now() - startedAt;
    const message =
      error instanceof Error && error.name === 'AbortError'
        ? `sandbox execution exceeded ${timeoutS}s (aborted)`
        : `sandbox request failed: ${error instanceof Error ? error.message : String(error)}`;
    return {
      output: {
        text: '',
        studio_error: { message },
        runtime: 'function-py'
      },
      usage: { inputTokens: 0, outputTokens: 0, bytesIn, bytesOut: 0, latencyMs }
    };
  } finally {
    clearTimeout(abortTimer);
  }

  const stdout = sandbox.stdout ?? '';
  const bytesOut = Buffer.byteLength(stdout, 'utf-8');
  const latencyMs = Date.now() - startedAt;
  const { studioResult, studioError, sentinelLine } = parseSentinel(stdout);

  if (!studioResult && !studioError) {
    // The worker never emitted a sentinel — treat as a hard failure and pass
    // along whatever stderr/exit info we have for debugging.
    return {
      output: {
        text: '',
        studio_error: {
          message: 'no Studio sentinel in sandbox stdout',
          traceback: (sandbox.stderr ?? '').slice(0, 4000)
        },
        exit_code: sandbox.exit_code,
        runtime: 'function-py'
      },
      usage: { inputTokens: 0, outputTokens: 0, bytesIn, bytesOut, latencyMs }
    };
  }

  const output: Record<string, unknown> = {
    text: sentinelLine ?? '',
    runtime: 'function-py'
  };
  if (studioResult) {
    output.studio_result = studioResult;
  }
  if (studioError) {
    output.studio_error = studioError;
  }

  return {
    output,
    usage: { inputTokens: 0, outputTokens: 0, bytesIn, bytesOut, latencyMs }
  };
}
