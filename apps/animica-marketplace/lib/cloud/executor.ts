// Animica Python Cloud — the executor (§7, §8, §9, §18, §19, §20, §21, §46).
//
// One invocation, end to end:
//
//   admit -> quote -> authorize funds -> record -> RUN (sandbox) -> meter -> price -> settle
//
// Everything the guest can do beyond pure computation arrives here as a host call, and every
// host call is authorized against SERVER-HELD context (the deployment's declared capabilities,
// the caller's grant, the remaining budget, the call depth) — never against anything the guest
// sent. That is what makes the capability system real rather than advisory.

import { prisma } from '../db';
import { ApiError } from '../api';
import { chat } from '../inference';
import { getHead, getAddressBalance } from '../chain';
import { isPrivateAddress } from '../workers';
import { postInTx } from '../ledger';
import { open as vaultOpen } from '../vault';
import { limits, runtime, type Capability } from './config';
import { activePolicy, quote, costOf, splitOf, priceForFailure, estimate, type Usage, type Policy } from './pricing';
import { settleExecution, checkAffordable } from './settle';
import {
  resolvePlan,
  consumeQuota,
  refundQuota,
  concurrencyFor,
  priorityFor,
  logRetentionDays,
  feeBpsForDeveloper,
  USAGE,
  type ResolvedPlan,
} from './entitlements';
import { runSandbox, SandboxBusyError, type HostCall, type HostReply, type SandboxLog } from './sandbox';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface InvokeInput {
  functionId: string;
  payload: unknown;
  /** null => anonymous caller on the free tier. */
  callerAccountId: string | null;
  callerKind?: 'user' | 'agent' | 'schedule' | 'internal';
  agentId?: string | null;
  /** Set for nested agent-to-agent calls. */
  parent?: { executionId: string; rootId: string; depth: number; budget: BudgetState } | null;
  /** Caller-supplied ceiling; the execution is refused if the quote exceeds it. */
  maxSpendNanm?: bigint | null;
}

export interface InvokeResult {
  requestId: string;
  status: 'succeeded' | 'failed' | 'timeout' | 'rejected';
  result?: unknown;
  error?: string;
  errorType?: string;
  traceback?: string;
  logs: SandboxLog[];
  stdout: string;
  durationMs: number;
  receipt: Receipt;
}

/** The customer-facing receipt (§85). Deliberately excludes internal cost/COGS fields. */
export interface Receipt {
  requestId: string;
  executionId: string;
  function: string;
  app?: string | null;
  version: number;
  timestamp: string;
  status: string;
  asset: 'ANM';
  grossNanm: string;
  platformFeeNanm: string;
  developerNanm: string;
  providerNanm: string;
  creditNanm: string;
  feeBps: number;
  usage: { durationMs: number; cpuMs: number; memoryMbMs: string; aiTokensIn: number; aiTokensOut: number };
  freeTier: boolean;
  ledgerRef: string;
}

/** Budget shared across a whole call tree so nested calls cannot exceed the root authorization. */
export interface BudgetState {
  spentNanm: bigint;
  maxNanm: bigint;
  calls: number;
  aiCalls: number;
  aiTokens: number;
}

// ---------------------------------------------------------------------------
// Public entry point
// ---------------------------------------------------------------------------

export async function invokeFunction(input: InvokeInput): Promise<InvokeResult> {
  // Resolve the CURRENT version, not the highest-numbered one. Rollback deliberately points
  // currentVersion at an older row while leaving newer versions in history (they are immutable),
  // so ordering by version desc would silently keep running the rolled-back code.
  const fn = await prisma.cloudFunction.findUnique({
    where: { id: input.functionId },
    include: {
      app: { select: { id: true, slug: true, status: true, suspendedAt: true } },
      owner: { select: { id: true, address: true, handle: true } },
      versions: {
        where: { version: { gt: 0 } },
        orderBy: { version: 'desc' },
      },
    },
  });
  if (!fn) throw new ApiError(404, 'not_found', 'function not found');
  if (fn.suspendedAt) throw new ApiError(403, 'suspended', fn.suspendedReason || 'this function has been suspended');
  if (fn.status !== 'PUBLISHED') throw new ApiError(409, 'not_active', 'this function has no active deployment');
  const version =
    fn.versions.find((v) => v.version === fn.currentVersion) ??
    // currentVersion 0 means "never deployed"; anything else pointing at a missing row is a
    // data-integrity fault, so fall back to the newest version rather than running nothing.
    (fn.currentVersion > 0 ? fn.versions[0] : undefined);
  if (!version) throw new ApiError(409, 'not_active', 'this function has no deployed version');
  if (fn.app && (fn.app.status === 'SUSPENDED' || fn.app.suspendedAt)) {
    throw new ApiError(403, 'suspended', 'the app that owns this function has been suspended');
  }

  const depth = input.parent?.depth ?? 0;
  if (depth > limits.maxCallDepth) {
    throw new ApiError(400, 'depth_exceeded', `maximum call depth (${limits.maxCallDepth}) exceeded`);
  }

  const policy = await activePolicy();
  const developerId = fn.ownerId;
  const feeBps = await feeBpsForDeveloper(developerId, policy.platformFeeBps);

  // --- admission control -----------------------------------------------------------------
  let plan: ResolvedPlan | null = null;
  let freeTier = false;
  if (input.callerAccountId) {
    plan = await resolvePlan(input.callerAccountId);
    const running = await prisma.cloudExecution.count({
      where: { callerAccountId: input.callerAccountId, status: { in: ['QUEUED', 'DISPATCHED', 'RUNNING'] } },
    });
    if (depth === 0 && running >= concurrencyFor(plan)) {
      throw new ApiError(429, 'concurrency_limit', `you already have ${running} executions running (plan limit ${concurrencyFor(plan)})`);
    }
  }

  const est = estimate(
    { timeoutMs: fn.timeoutMs, memoryMb: fn.memoryMb, surchargeNanm: fn.perCallNanm, feeBps },
    policy,
  );

  // Caller's ceiling, then the shared tree budget for nested calls.
  const budget: BudgetState = input.parent?.budget ?? {
    spentNanm: 0n,
    maxNanm: input.maxSpendNanm && input.maxSpendNanm > 0n ? input.maxSpendNanm : est.maxNanm,
    calls: 0,
    aiCalls: 0,
    aiTokens: 0,
  };
  if (budget.spentNanm + est.maxNanm > budget.maxNanm && depth > 0) {
    throw new ApiError(402, 'budget_exceeded', 'this call would exceed the authorized execution budget');
  }

  // --- funding ---------------------------------------------------------------------------
  if (input.callerAccountId) {
    const quotaKey = USAGE.executions;
    const q = await consumeQuota(input.callerAccountId, quotaKey, 1, plan!);
    if (!q.ok) {
      throw new ApiError(
        402,
        'quota_exceeded',
        `You have used ${q.used} of ${q.limit} executions this month on the ${plan!.key} plan. Upgrade at /cloud/pricing.`,
      );
    }
    // Inside the included allowance on a paid plan, or the free allowance on Free: the caller
    // does not pay ANM for their OWN function. Calling someone else's paid function always costs.
    const ownFunction = input.callerAccountId === developerId;
    freeTier = ownFunction;
    if (!freeTier) {
      const afford = await checkAffordable(input.callerAccountId, est.maxNanm);
      if (!afford.ok) {
        await refundQuota(input.callerAccountId, quotaKey, 1);
        throw new ApiError(
          402,
          'insufficient_funds',
          `This call needs up to ${est.maxNanm} nANM; your available balance is ${afford.availableNanm} nANM. Deposit ANM to continue.`,
        );
      }
    }
  } else {
    // Anonymous: only free, public, zero-surcharge functions are reachable, and only inside
    // the free-tier allowance measured per IP by the route layer.
    if (fn.requiresAuth || fn.perCallNanm > 0n) {
      throw new ApiError(401, 'auth_required', 'this function requires authentication');
    }
    // An anonymous caller cannot be billed, so their worst case has to be small. Without a
    // concurrency cap here, unauthenticated traffic could hold every sandbox slot on a box that
    // also runs the mainnet node — the per-account cap above does not apply to them.
    const anonRunning = await prisma.cloudExecution.count({
      where: { callerAccountId: null, status: { in: ['QUEUED', 'DISPATCHED', 'RUNNING'] } },
    });
    if (anonRunning >= limits.anonMaxConcurrent * Math.max(1, limits.globalConcurrency - 2)) {
      throw new ApiError(429, 'busy', 'Too many anonymous executions in flight. Sign in for dedicated capacity.');
    }
    freeTier = true;
  }

  // --- record ----------------------------------------------------------------------------
  const requestId = `rq_${cryptoRandom()}`;
  const exec = await prisma.cloudExecution.create({
    data: {
      requestId,
      functionId: fn.id,
      versionId: version.id,
      appId: fn.appId,
      agentId: input.agentId ?? null,
      callerAccountId: input.callerAccountId,
      callerKind: input.callerKind ?? 'user',
      developerAccountId: developerId,
      parentExecutionId: input.parent?.executionId ?? null,
      rootId: input.parent?.rootId ?? null,
      depth,
      status: 'RUNNING',
      lane: 'local',
      quotedNanm: est.maxNanm,
      feeBps,
      startedAt: new Date(),
      pricingPolicyId: policy.id,
    },
  });
  if (!input.parent) {
    await prisma.cloudExecution.update({ where: { id: exec.id }, data: { rootId: exec.id } });
  }

  // --- run -------------------------------------------------------------------------------
  const meter: Meter = {
    aiTokensIn: 0,
    aiTokensOut: 0,
    aiCalls: 0,
    egressBytes: 0,
    nestedNanm: 0n,
    hostCalls: 0,
    chainCalls: 0,
    httpCalls: 0,
    stateCalls: 0,
  };
  const secrets = await loadSecrets(fn.id, developerId);
  const grantedCaps = (fn.capabilities || []) as Capability[];

  let sandboxResult;
  try {
    sandboxResult = await runSandbox(
      {
        requestId,
        source: version.source,
        entrypoint: version.entrypoint || fn.entrypoint,
        request: input.payload ?? {},
        meta: {
          request_id: requestId,
          function: fn.slug,
          version: version.version,
          owner: fn.owner.address,
          caller: input.callerAccountId ? 'account' : 'anonymous',
          deadline_ms: fn.timeoutMs,
        },
        // Anonymous callers get a tighter clock: they cannot be billed for the resources they
        // consume, so an unauthenticated request must never be able to occupy a slot for the
        // full 5-minute platform maximum.
        timeoutMs: input.callerAccountId ? fn.timeoutMs : Math.min(fn.timeoutMs, limits.anonMaxTimeoutMs),
        memoryMb: fn.memoryMb,
        secrets,
      },
      makeHostHandler({
        exec,
        fn,
        grantedCaps,
        callerAccountId: input.callerAccountId,
        developerId,
        budget,
        meter,
        policy,
        depth,
      }),
    );
  } catch (e: any) {
    if (e instanceof SandboxBusyError) {
      await prisma.cloudExecution.update({
        where: { id: exec.id },
        data: { status: 'REJECTED', errorCode: 'busy', error: e.message, finishedAt: new Date() },
      });
      if (input.callerAccountId) await refundQuota(input.callerAccountId, USAGE.executions, 1);
      throw new ApiError(503, 'busy', 'The execution fleet is at capacity. Please retry shortly.');
    }
    throw e;
  }

  // --- meter -----------------------------------------------------------------------------
  // Billed CPU is the HOST-measured container wall time: that is the resource Animica actually
  // reserves, and unlike a guest-reported number it cannot be understated by hostile code.
  const billedMs = Math.max(1, sandboxResult.wallMs);
  const usage: Usage = {
    cpuMs: billedMs,
    memoryMbMs: BigInt(billedMs) * BigInt(fn.memoryMb),
    aiTokensIn: meter.aiTokensIn,
    aiTokensOut: meter.aiTokensOut,
    egressBytes: meter.egressBytes + byteLen(sandboxResult.result),
    gpuMs: 0,
  };

  const succeeded = sandboxResult.status === 'ok';
  const status =
    sandboxResult.status === 'ok'
      ? 'SUCCEEDED'
      : sandboxResult.status === 'timeout'
        ? 'TIMEOUT'
        : 'FAILED';

  // §46: a failed run still consumed real resources, so it is charged at metered cost with no
  // developer surcharge and no margin uplift — Animica never profits from a broken function.
  const priced = succeeded
    ? quote(usage, policy, { surchargeNanm: fn.perCallNanm, feeBps })
    : null;
  const priceNanm = succeeded ? priced!.totalNanm : priceForFailure(usage, policy);
  const split = splitOf(priceNanm, succeeded ? feeBps : 10_000, policy.providerShareBps, false);
  const cogs = costOf(usage, policy);

  await prisma.cloudExecution.update({
    where: { id: exec.id },
    data: {
      status: status as any,
      finishedAt: new Date(),
      durationMs: sandboxResult.wallMs,
      cpuMs: billedMs,
      memoryMbMs: usage.memoryMbMs,
      aiTokensIn: meter.aiTokensIn,
      aiTokensOut: meter.aiTokensOut,
      aiCalls: meter.aiCalls,
      egressBytes: usage.egressBytes,
      bytesOut: byteLen(sandboxResult.result),
      errorCode: succeeded ? null : sandboxResult.errorType || sandboxResult.status,
      error: succeeded ? null : (sandboxResult.error || '').slice(0, 2000),
      httpStatus: succeeded ? 200 : sandboxResult.status === 'timeout' ? 504 : 500,
    },
  });

  await persistLogs(exec.id, sandboxResult.logs, sandboxResult.stdout, plan);

  // --- settle ----------------------------------------------------------------------------
  let settlement = {
    priceNanm: 0n,
    platformFeeNanm: 0n,
    developerNanm: 0n,
    providerNanm: 0n,
    creditNanm: 0n,
  };
  if (freeTier || priceNanm === 0n) {
    // Free-tier work is real cost to Animica with no revenue: record it so the free-tier
    // COGS line on /admin/profitability is truthful (§78).
    await prisma.cloudExecution.update({
      where: { id: exec.id },
      data: {
        billed: true,
        freeTier: true,
        priceNanm: 0n,
        cogsNanm: cogs.totalNanm,
        cogsAiNanm: cogs.aiNanm,
        cogsComputeNanm: cogs.computeNanm,
        cogsInfraNanm: cogs.infraNanm,
        contributionNanm: -cogs.totalNanm,
      },
    });
  } else {
    const r = await settleExecution({
      executionId: exec.id,
      callerAccountId: input.callerAccountId,
      developerAccountId: developerId,
      priceNanm,
      platformFeeNanm: split.platformFeeNanm,
      developerNanm: split.developerNanm,
      providerNanm: 0n,
      cogs,
      feeBps: split.feeBps,
      policy,
    });
    settlement = {
      priceNanm,
      platformFeeNanm: r.platformFeeNanm,
      developerNanm: r.developerNanm,
      providerNanm: r.providerNanm,
      creditNanm: r.creditNanm,
    };
    budget.spentNanm += priceNanm;
    await raiseMarginAlertIfNeeded(exec.id, r.contributionNanm, priceNanm);
  }

  // Compute-unit and AI-unit quotas are metered AFTER the fact (we only know real usage now).
  if (input.callerAccountId) {
    const cpuUnits = Math.max(1, Math.round(billedMs / 1000));
    await consumeQuota(input.callerAccountId, USAGE.computeUnits, cpuUnits, plan!).catch(() => {});
    const aiUnits = Math.ceil((meter.aiTokensIn + meter.aiTokensOut) / 1000);
    if (aiUnits > 0) await consumeQuota(input.callerAccountId, USAGE.aiUnits, aiUnits, plan!).catch(() => {});
  }

  return {
    requestId,
    status: succeeded ? 'succeeded' : sandboxResult.status === 'timeout' ? 'timeout' : 'failed',
    result: sandboxResult.result,
    error: sandboxResult.error,
    errorType: sandboxResult.errorType,
    traceback: sandboxResult.traceback,
    logs: sandboxResult.logs,
    stdout: sandboxResult.stdout,
    durationMs: sandboxResult.wallMs,
    receipt: {
      requestId,
      executionId: exec.id,
      function: `${fn.owner.handle ?? fn.owner.address.slice(0, 12)}/${fn.slug}`,
      app: fn.app?.slug ?? null,
      version: version.version,
      timestamp: new Date().toISOString(),
      status: succeeded ? 'succeeded' : 'failed',
      asset: 'ANM',
      grossNanm: settlement.priceNanm.toString(),
      platformFeeNanm: settlement.platformFeeNanm.toString(),
      developerNanm: settlement.developerNanm.toString(),
      providerNanm: settlement.providerNanm.toString(),
      creditNanm: settlement.creditNanm.toString(),
      feeBps: split.feeBps,
      usage: {
        durationMs: sandboxResult.wallMs,
        cpuMs: billedMs,
        memoryMbMs: usage.memoryMbMs.toString(),
        aiTokensIn: meter.aiTokensIn,
        aiTokensOut: meter.aiTokensOut,
      },
      freeTier,
      ledgerRef: exec.id,
    },
  };
}

// ---------------------------------------------------------------------------
// Capability broker
// ---------------------------------------------------------------------------

interface Meter {
  aiTokensIn: number;
  aiTokensOut: number;
  aiCalls: number;
  egressBytes: number;
  nestedNanm: bigint;
  /** Every host call, regardless of op. Bounds RPC/outbound amplification (see hostCallGuard). */
  hostCalls: number;
  chainCalls: number;
  httpCalls: number;
  stateCalls: number;
}

interface HandlerCtx {
  exec: { id: string; requestId: string; rootId: string | null };
  fn: any;
  grantedCaps: Capability[];
  callerAccountId: string | null;
  developerId: string;
  budget: BudgetState;
  meter: Meter;
  policy: Policy;
  depth: number;
}

function deny(message: string): HostReply {
  return { ok: false, code: 'capability_denied', error: message };
}
function budgetErr(message: string): HostReply {
  return { ok: false, code: 'budget_exceeded', error: message };
}

function makeHostHandler(ctx: HandlerCtx) {
  const has = (cap: Capability) => ctx.grantedCaps.includes(cap);

  /**
   * Bound EVERY privileged operation, not just the expensive-looking ones.
   *
   * Without this, a single cheap execution could issue an unbounded stream of chain.* reads or
   * http.fetch calls. That is not merely a cost problem: the Animica mainnet node runs on this
   * same box and has a documented history of RPC/CPU starvation, so guest-driven RPC
   * amplification is an outage risk for the chain itself.
   */
  const guard = (op: string): HostReply | null => {
    ctx.meter.hostCalls += 1;
    if (ctx.meter.hostCalls > limits.maxHostCallsPerExecution) {
      return budgetErr(`host-call limit (${limits.maxHostCallsPerExecution} per execution) reached`);
    }
    if (op.startsWith('chain.')) {
      ctx.meter.chainCalls += 1;
      if (ctx.meter.chainCalls > limits.maxChainCallsPerExecution) {
        return budgetErr(`chain read limit (${limits.maxChainCallsPerExecution} per execution) reached`);
      }
    } else if (op === 'http.fetch') {
      ctx.meter.httpCalls += 1;
      if (ctx.meter.httpCalls > limits.maxHttpCallsPerExecution) {
        return budgetErr(`outbound HTTP limit (${limits.maxHttpCallsPerExecution} per execution) reached`);
      }
    } else if (op.startsWith('state.')) {
      ctx.meter.stateCalls += 1;
      if (ctx.meter.stateCalls > limits.maxStateCallsPerExecution) {
        return budgetErr(`state operation limit (${limits.maxStateCallsPerExecution} per execution) reached`);
      }
    }
    return null;
  };

  return async (call: HostCall): Promise<HostReply> => {
    const blocked = guard(call.op);
    if (blocked) return blocked;
    switch (call.op) {
      // ---- AI (§21) --------------------------------------------------------------------
      case 'ai.infer': {
        if (!has('AI_INFERENCE')) return deny('this function does not declare the AI_INFERENCE capability');
        // Animica AI is served by the treasury-funded bridge, so an anonymous execution spends
        // platform money with no counterparty. Give unauthenticated callers a much smaller AI
        // budget than authenticated ones, who are metered and billed for what they use.
        const aiCap = ctx.callerAccountId ? limits.maxAiCallsPerExecution : limits.anonMaxAiCallsPerExecution;
        if (ctx.meter.aiCalls >= aiCap) {
          return budgetErr(`AI call limit (${aiCap} per execution) reached`);
        }
        if (ctx.meter.aiTokensIn + ctx.meter.aiTokensOut >= limits.maxAiTokensPerExecution) {
          return budgetErr('AI token budget for this execution is exhausted');
        }
        const messages = Array.isArray(call.args.messages) ? call.args.messages : [];
        if (!messages.length) return { ok: false, code: 'bad_request', error: 'messages must be a non-empty array' };
        const maxTokens = Math.min(Number(call.args.max_tokens) || 1024, limits.maxAiTokensPerExecution);
        try {
          const r = await chat(
            messages.slice(-20).map((m: any) => ({
              role: String(m.role ?? 'user'),
              content: String(m.content ?? '').slice(0, 20_000),
            })) as any,
            { paid: false, maxTokens, temperature: Number(call.args.temperature) || undefined, timeoutMs: 60_000 },
          );
          ctx.meter.aiCalls += 1;
          ctx.meter.aiTokensIn += r.tokensIn || 0;
          ctx.meter.aiTokensOut += r.tokensOut || 0;
          return { ok: true, result: { text: r.text, model: r.model, tokens_in: r.tokensIn, tokens_out: r.tokensOut } };
        } catch (e: any) {
          return { ok: false, code: 'ai_unavailable', error: `AI inference failed: ${String(e?.message ?? e).slice(0, 200)}` };
        }
      }

      // ---- chain reads -----------------------------------------------------------------
      case 'chain.head': {
        if (!has('READ_CHAIN')) return deny('this function does not declare the READ_CHAIN capability');
        try {
          return { ok: true, result: await getHead() };
        } catch (e: any) {
          return { ok: false, code: 'rpc_error', error: String(e?.message ?? e).slice(0, 200) };
        }
      }
      case 'chain.balance': {
        if (!has('READ_CHAIN')) return deny('this function does not declare the READ_CHAIN capability');
        const address = String(call.args.address ?? '');
        if (!/^anim1[0-9a-z]{20,}$/.test(address)) {
          return { ok: false, code: 'bad_request', error: 'address must be a bech32m anim1... address' };
        }
        try {
          const b = await getAddressBalance(address);
          return { ok: true, result: { nanm: b.nanm.toString(), as_of_height: b.asOfHeight } };
        } catch (e: any) {
          return { ok: false, code: 'rpc_error', error: String(e?.message ?? e).slice(0, 200) };
        }
      }

      // ---- spending (§20) --------------------------------------------------------------
      case 'wallet.balance': {
        if (!ctx.callerAccountId) return deny('anonymous executions have no wallet');
        const a = await prisma.account.findUnique({
          where: { id: ctx.callerAccountId },
          select: { balanceNanm: true },
        });
        return { ok: true, result: { nanm: (a?.balanceNanm ?? 0n).toString() } };
      }
      case 'wallet.pay': {
        if (!has('SPEND_ANM')) return deny('this function does not declare the SPEND_ANM capability');
        if (!ctx.callerAccountId) return deny('anonymous executions cannot spend');
        return payFromGrant(ctx, call.args);
      }

      // ---- nested calls (§18) ----------------------------------------------------------
      case 'call': {
        if (!has('CALL_FUNCTION') && !has('CALL_APP')) {
          return deny('this function does not declare the CALL_FUNCTION capability');
        }
        if (ctx.budget.calls >= limits.maxCallsPerExecution) {
          return budgetErr(`nested call limit (${limits.maxCallsPerExecution}) reached`);
        }
        if (ctx.depth + 1 > limits.maxCallDepth) {
          return { ok: false, code: 'depth_exceeded', error: `maximum call depth (${limits.maxCallDepth}) reached` };
        }
        const target = String(call.args.target ?? '');
        const targetFn = await resolveTarget(target);
        if (!targetFn) return { ok: false, code: 'not_found', error: `no such function: ${target}` };
        if (targetFn.id === ctx.fn.id) {
          return { ok: false, code: 'recursion', error: 'a function may not call itself' };
        }
        ctx.budget.calls += 1;
        try {
          const nested = await invokeFunction({
            functionId: targetFn.id,
            payload: call.args.payload ?? {},
            callerAccountId: ctx.callerAccountId,
            callerKind: 'agent',
            parent: {
              executionId: ctx.exec.id,
              rootId: ctx.exec.rootId ?? ctx.exec.id,
              depth: ctx.depth + 1,
              budget: ctx.budget,
            },
          });
          return {
            ok: true,
            result: {
              status: nested.status,
              result: nested.result,
              error: nested.error,
              request_id: nested.requestId,
              cost_nanm: nested.receipt.grossNanm,
            },
          };
        } catch (e: any) {
          const code = e?.code === 'budget_exceeded' || e?.code === 'insufficient_funds' ? 'budget_exceeded' : 'call_failed';
          return { ok: false, code, error: String(e?.message ?? e).slice(0, 300) };
        }
      }

      // ---- state ----------------------------------------------------------------------
      case 'state.get':
      case 'state.set':
      case 'state.delete': {
        if (!has('PERSIST_STATE')) return deny('this function does not declare the PERSIST_STATE capability');
        return stateOp(ctx, call);
      }

      // ---- mediated HTTP ---------------------------------------------------------------
      case 'http.fetch': {
        if (!has('HTTP_FETCH')) return deny('this function does not declare the HTTP_FETCH capability');
        return httpFetch(ctx, call.args);
      }

      default:
        return { ok: false, code: 'unknown_op', error: `unsupported host operation: ${call.op}` };
    }
  };
}

// ---------------------------------------------------------------------------
// Capability implementations
// ---------------------------------------------------------------------------

async function payFromGrant(ctx: HandlerCtx, args: any): Promise<HostReply> {
  const to = String(args.to ?? '');
  let amount: bigint;
  try {
    amount = BigInt(args.amount_nanm ?? 0);
  } catch {
    return { ok: false, code: 'bad_request', error: 'amount_nanm must be an integer' };
  }
  if (amount <= 0n) return { ok: false, code: 'bad_request', error: 'amount_nanm must be > 0' };
  if (!/^anim1[0-9a-z]{20,}$/.test(to)) {
    return { ok: false, code: 'bad_request', error: 'recipient must be a bech32m anim1... address' };
  }

  const subjectId = ctx.fn.appId ?? ctx.fn.id;
  const subjectKind = ctx.fn.appId ? 'app' : 'function';
  const grant = await prisma.cloudGrant.findUnique({
    where: {
      accountId_subjectKind_subjectId: { accountId: ctx.callerAccountId!, subjectKind, subjectId },
    },
  });
  if (!grant || grant.revokedAt || (grant.expiresAt && grant.expiresAt <= new Date())) {
    return deny('you have not authorized this app to spend ANM on your behalf');
  }
  if (!grant.capabilities.includes('SPEND_ANM')) {
    return deny('your authorization for this app does not include SPEND_ANM');
  }
  if (grant.maxPerCallNanm > 0n && amount > grant.maxPerCallNanm) {
    return budgetErr(`per-payment limit is ${grant.maxPerCallNanm} nANM`);
  }
  if (grant.allowedPayees.length > 0 && !grant.allowedPayees.includes(to)) {
    return deny('recipient is not in your approved payee list for this app');
  }
  if (grant.maxPerExecNanm > 0n && ctx.meter.nestedNanm + amount > grant.maxPerExecNanm) {
    return budgetErr(`per-execution spend limit is ${grant.maxPerExecNanm} nANM`);
  }
  if (ctx.budget.spentNanm + amount > ctx.budget.maxNanm) {
    return budgetErr('this payment would exceed the authorized execution budget');
  }

  const dayKey = new Date().toISOString().slice(0, 10);
  const payee = await prisma.account.findUnique({ where: { address: to }, select: { id: true } });
  if (!payee) return { ok: false, code: 'not_found', error: 'recipient has no Animica account on this platform' };

  try {
    const txid = await prisma.$transaction(async (tx) => {
      // Atomic daily-cap claim: reset the counter if the UTC day rolled over, then apply the
      // increment conditionally so concurrent executions cannot both spend the last allowance.
      const fresh = await tx.cloudGrant.findUnique({ where: { id: grant.id } });
      if (!fresh) throw new ApiError(403, 'capability_denied', 'authorization was revoked');
      const spentToday = fresh.spendDayKey === dayKey ? fresh.spentTodayNanm : 0n;
      if (fresh.dailyCapNanm > 0n && spentToday + amount > fresh.dailyCapNanm) {
        throw new ApiError(402, 'budget_exceeded', `daily spend cap of ${fresh.dailyCapNanm} nANM reached`);
      }
      const claimed = await tx.cloudGrant.updateMany({
        where: { id: grant.id, spentTodayNanm: fresh.spentTodayNanm, spendDayKey: fresh.spendDayKey, revokedAt: null },
        data: { spentTodayNanm: spentToday + amount, spendDayKey: dayKey },
      });
      if (claimed.count !== 1) throw new ApiError(409, 'budget_exceeded', 'concurrent spend, please retry');

      await postInTx(tx, ctx.callerAccountId!, -amount, 'PURCHASE_DEBIT', ctx.exec.id, `app payment to ${to.slice(0, 16)}…`);
      await postInTx(tx, payee.id, amount, 'SALE_CREDIT', ctx.exec.id, 'app payment received');
      return ctx.exec.id;
    });
    ctx.meter.nestedNanm += amount;
    ctx.budget.spentNanm += amount;
    return { ok: true, result: { ok: true, amount_nanm: amount.toString(), to, ref: txid } };
  } catch (e: any) {
    if (e?.code === 'INSUFFICIENT_FUNDS') {
      return { ok: false, code: 'insufficient_funds', error: 'your balance is too low for this payment' };
    }
    return { ok: false, code: e?.code ?? 'pay_failed', error: String(e?.message ?? e).slice(0, 300) };
  }
}

const STATE_MAX_KEYS = 200;
const STATE_MAX_VALUE_BYTES = 16 * 1024;

async function stateOp(ctx: HandlerCtx, call: HostCall): Promise<HostReply> {
  const key = String(call.args.key ?? '').slice(0, 128);
  if (!key) return { ok: false, code: 'bad_request', error: 'key is required' };
  const scope = `fn:${ctx.fn.id}`;
  if (call.op === 'state.get') {
    const row = await prisma.cloudSecret.findFirst({ where: { ownerId: ctx.developerId, name: `__state__${scope}__${key}` } });
    if (!row) return { ok: true, result: { value: null } };
    try {
      return { ok: true, result: { value: JSON.parse(vaultOpen(row.ciphertext)) } };
    } catch {
      return { ok: true, result: { value: null } };
    }
  }
  if (call.op === 'state.delete') {
    await prisma.cloudSecret.deleteMany({ where: { ownerId: ctx.developerId, name: `__state__${scope}__${key}` } });
    return { ok: true, result: { ok: true } };
  }
  const encoded = JSON.stringify(call.args.value ?? null);
  if (encoded.length > STATE_MAX_VALUE_BYTES) {
    return { ok: false, code: 'too_large', error: `state values are limited to ${STATE_MAX_VALUE_BYTES} bytes` };
  }
  const count = await prisma.cloudSecret.count({ where: { ownerId: ctx.developerId, name: { startsWith: `__state__${scope}__` } } });
  if (count >= STATE_MAX_KEYS) {
    return { ok: false, code: 'quota_exceeded', error: `state is limited to ${STATE_MAX_KEYS} keys per function` };
  }
  const { seal } = await import('../vault');
  await prisma.cloudSecret.upsert({
    where: { ownerId_functionId_name: { ownerId: ctx.developerId, functionId: ctx.fn.id, name: `__state__${scope}__${key}` } },
    create: { ownerId: ctx.developerId, functionId: ctx.fn.id, name: `__state__${scope}__${key}`, ciphertext: seal(encoded) },
    update: { ciphertext: seal(encoded) },
  });
  return { ok: true, result: { ok: true } };
}

const HTTP_MAX_BYTES = 512 * 1024;

async function httpFetch(ctx: HandlerCtx, args: any): Promise<HostReply> {
  const raw = String(args.url ?? '');
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return { ok: false, code: 'bad_request', error: 'invalid URL' };
  }
  // Same SSRF discipline the Workers webhook path uses: https only, no credentials, no
  // private/loopback/link-local targets, no IPv6 literals, and the trailing-dot bypass closed.
  if (url.protocol !== 'https:') return { ok: false, code: 'blocked', error: 'only https URLs are allowed' };
  if (url.username || url.password) return { ok: false, code: 'blocked', error: 'credentials in the URL are not allowed' };
  const host = url.hostname.toLowerCase().replace(/\.+$/, '');
  if (!host || host.includes(':') || host.startsWith('[')) {
    return { ok: false, code: 'blocked', error: 'IPv6 literal targets are not allowed' };
  }
  if (host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local') || host.endsWith('.internal')) {
    return { ok: false, code: 'blocked', error: 'internal hostnames are not allowed' };
  }
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host) && isPrivateAddress(host)) {
    return { ok: false, code: 'blocked', error: 'private/loopback targets are not allowed' };
  }
  // Re-resolve at request time and re-check: DNS can move between validation and fetch.
  try {
    const dns = await import('node:dns/promises');
    const addrs = await dns.lookup(host, { all: true });
    if (addrs.some((a) => isPrivateAddress(a.address))) {
      return { ok: false, code: 'blocked', error: 'host resolves to a private address' };
    }
  } catch {
    return { ok: false, code: 'blocked', error: 'host does not resolve' };
  }

  const method = String(args.method ?? 'GET').toUpperCase();
  if (!['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'].includes(method)) {
    return { ok: false, code: 'bad_request', error: `unsupported method ${method}` };
  }
  const controller = new AbortController();
  const timeout = Math.min(Math.max(Number(args.timeout) || 10, 1), 30);
  const timer = setTimeout(() => controller.abort(), timeout * 1000);
  try {
    const headers: Record<string, string> = { 'user-agent': 'AnimicaPythonCloud/1.0' };
    for (const [k, v] of Object.entries(args.headers ?? {})) {
      const key = String(k).toLowerCase();
      // Never let a function forge auth/host headers or smuggle our own cookies.
      if (['host', 'cookie', 'authorization', 'x-forwarded-for'].includes(key)) continue;
      headers[key] = String(v).slice(0, 1024);
    }
    const res = await fetch(url.toString(), {
      method,
      headers,
      body: args.body != null && method !== 'GET' && method !== 'HEAD' ? String(args.body).slice(0, 256 * 1024) : undefined,
      signal: controller.signal,
      redirect: 'manual', // a 3xx to an internal host would defeat every check above
    });
    const buf = await res.arrayBuffer();
    const body = Buffer.from(buf.slice(0, HTTP_MAX_BYTES)).toString('utf8');
    ctx.meter.egressBytes += body.length;
    return {
      ok: true,
      result: {
        status: res.status,
        headers: Object.fromEntries([...res.headers.entries()].slice(0, 40)),
        body,
        truncated: buf.byteLength > HTTP_MAX_BYTES,
      },
    };
  } catch (e: any) {
    return { ok: false, code: 'fetch_failed', error: String(e?.message ?? e).slice(0, 200) };
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function resolveTarget(target: string) {
  const clean = target.trim().replace(/^\/+/, '');
  const [ownerPart, fnPart] = clean.includes('/') ? clean.split('/') : [null, clean];
  if (ownerPart) {
    const owner = await prisma.account.findFirst({
      where: { OR: [{ handle: ownerPart }, { address: ownerPart }] },
      select: { id: true },
    });
    if (!owner) return null;
    return prisma.cloudFunction.findFirst({
      where: { ownerId: owner.id, slug: fnPart, status: 'PUBLISHED', visibility: { in: ['PUBLIC', 'UNLISTED'] } },
      select: { id: true },
    });
  }
  return prisma.cloudFunction.findFirst({
    where: { slug: fnPart, status: 'PUBLISHED', visibility: 'PUBLIC' },
    select: { id: true },
  });
}

async function loadSecrets(functionId: string, ownerId: string): Promise<Record<string, string>> {
  const rows = await prisma.cloudSecret.findMany({
    where: { ownerId, OR: [{ functionId }, { functionId: null }], NOT: { name: { startsWith: '__state__' } } },
  });
  const out: Record<string, string> = {};
  for (const r of rows) {
    try {
      out[r.name] = vaultOpen(r.ciphertext);
    } catch {
      // A secret that cannot be unsealed is skipped rather than failing the execution; the
      // developer sees it flagged in the console.
    }
  }
  if (rows.length) {
    await prisma.cloudSecret
      .updateMany({ where: { id: { in: rows.map((r) => r.id) } }, data: { lastUsedAt: new Date() } })
      .catch(() => {});
  }
  return out;
}

/** Redact anything that looks like an injected secret before logs are stored (§30). */
function redact(text: string, secrets: string[]): string {
  let out = text;
  for (const s of secrets) {
    if (s && s.length >= 6) out = out.split(s).join('«redacted»');
  }
  return out;
}

async function persistLogs(executionId: string, logs: SandboxLog[], stdout: string, plan: ResolvedPlan | null) {
  const rows: { executionId: string; level: string; message: string; seq: number }[] = [];
  let seq = 0;
  for (const l of logs.slice(0, limits.maxLogLines)) {
    rows.push({ executionId, level: l.level, message: l.message.slice(0, limits.maxLogLineChars), seq: seq++ });
  }
  for (const line of stdout.split('\n').slice(0, limits.maxLogLines)) {
    if (!line.trim()) continue;
    rows.push({ executionId, level: 'stdout', message: line.slice(0, limits.maxLogLineChars), seq: seq++ });
    if (seq >= limits.maxLogLines) break;
  }
  if (rows.length) await prisma.cloudExecutionLog.createMany({ data: rows }).catch(() => {});
  void plan; // retention is enforced by the janitor worker using logRetentionDays(plan)
}

async function raiseMarginAlertIfNeeded(executionId: string, contributionNanm: bigint, priceNanm: bigint) {
  if (contributionNanm >= 0n) return;
  // Only alert on material losses so the console stays signal, not noise.
  const threshold = BigInt(process.env.CLOUD_NEG_MARGIN_ALERT_NANM ?? '1000000');
  if (-contributionNanm < threshold) return;
  await prisma.financeAlert
    .create({
      data: {
        kind: 'negative_margin',
        severity: 'warn',
        title: 'Execution served below cost',
        subject: executionId,
        detail: JSON.stringify({ executionId, contributionNanm: contributionNanm.toString(), priceNanm: priceNanm.toString() }),
      },
    })
    .catch(() => {});
}

function byteLen(v: unknown): number {
  if (v == null) return 0;
  try {
    return Buffer.byteLength(JSON.stringify(v), 'utf8');
  } catch {
    return 0;
  }
}

function cryptoRandom(): string {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  return require('node:crypto').randomBytes(12).toString('hex');
}

export { redact, logRetentionDays, priorityFor, runtime };
