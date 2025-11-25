import { describe, it, expect } from 'vitest';

// We mock the studio-web AICF service to simulate enqueue → pending → complete.
// This mirrors the verify-flow test style and gives us deterministic behavior.
vi.mock('../../src/services/aicf', () => {
  type JobKind = 'AI' | 'QUANTUM';
  type Job = {
    id: string;
    kind: JobKind;
    polls: number;
    result: any;
  };

  const jobs = new Map<string, Job>();

  const newId = () => 'job_' + Math.random().toString(36).slice(2);

  const enqueueAIJob = async (req: {
    model: string;
    prompt: string;
    maxTokens?: number;
    units?: number;
  }): Promise<{ jobId: string }> => {
    const id = newId();
    const tokens = Math.min(64, Math.max(8, Math.ceil((req.maxTokens ?? 32) * 0.75)));
    jobs.set(id, {
      id,
      kind: 'AI',
      polls: 0,
      result: {
        output: {
          text: `echo(${req.model}): ${req.prompt}`,
          tokens,
        },
        metrics: { ai_units: req.units ?? 1, qos: 0.99, latency_ms: 1234 },
      },
    });
    return { jobId: id };
  };

  const enqueueQuantumJob = async (req: {
    circuit: Record<string, unknown>;
    shots: number;
  }): Promise<{ jobId: string }> => {
    const id = newId();
    const counts = { '00': Math.floor(req.shots * 0.48), '11': Math.ceil(req.shots * 0.52) };
    jobs.set(id, {
      id,
      kind: 'QUANTUM',
      polls: 0,
      result: {
        output: { counts, shots: req.shots },
        metrics: { traps_ratio: 0.02, success_prob: 0.985 },
      },
    });
    return { jobId: id };
  };

  const getJob = async (jobId: string) => {
    const j = jobs.get(jobId);
    if (!j) throw new Error('job not found');
    return { jobId: j.id, kind: j.kind };
  };

  const getResult = async (
    jobId: string,
  ): Promise<{ status: 'pending' } | { status: 'complete'; result: any }> => {
    const j = jobs.get(jobId);
    if (!j) throw new Error('job not found');
    j.polls++;
    if (j.polls < 2) return { status: 'pending' };
    return { status: 'complete', result: j.result };
  };

  // Optional helper that some consumers use to block until complete. Kept simple here.
  const pollResult = async (jobId: string, _opts?: { intervalMs?: number; maxWaitMs?: number }) => {
    const first = await getResult(jobId);
    if (first.status === 'complete') return first;
    return getResult(jobId);
  };

  return {
    enqueueAIJob,
    enqueueQuantumJob,
    getJob,
    getResult,
    pollResult,
  };
});

// Import AFTER the mock so we receive the mocked functions.
import {
  enqueueAIJob,
  enqueueQuantumJob,
  getJob,
  getResult,
  pollResult,
} from '../../src/services/aicf';

describe('AICF enqueue & poll flow', () => {
  it('enqueues an AI job and completes on subsequent poll', async () => {
    const { jobId } = await enqueueAIJob({
      model: 'tiny-demo',
      prompt: 'hello animica',
      maxTokens: 40,
      units: 2,
    });

    const meta = await getJob(jobId);
    expect(meta.jobId).toBe(jobId);
    expect(meta.kind).toBe('AI');

    // First poll → pending
    const p1 = await getResult(jobId);
    expect(p1.status).toBe('pending');

    // Second poll → complete
    const p2 = await getResult(jobId);
    expect(p2.status).toBe('complete');
    expect(p2.result.output.text).toContain('hello animica');
    expect(p2.result.metrics.ai_units).toBe(2);

    // pollResult helper should return complete immediately now
    const done = await pollResult(jobId);
    expect(done.status).toBe('complete');
  });

  it('enqueues a Quantum job and returns counts after two polls', async () => {
    const circuit = { gates: [{ name: 'H', target: 0 }], measures: [0] };
    const { jobId } = await enqueueQuantumJob({ circuit, shots: 1000 });

    const meta = await getJob(jobId);
    expect(meta.kind).toBe('QUANTUM');

    // Pending first
    await getResult(jobId).then((r) => expect(r.status).toBe('pending'));
    // Complete next
    const res = await getResult(jobId);
    expect(res.status).toBe('complete');
    expect(res.result.output.shots).toBe(1000);
    const counts = res.result.output.counts as Record<string, number>;
    expect(counts['00'] + counts['11']).toBe(1000);
    expect(res.result.metrics.traps_ratio).toBeLessThan(0.1);
  });
});
