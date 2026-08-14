import { NextRequest } from 'next/server';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { publicOk, publicPreflight, err, ApiError } from '@/lib/api';
import { runtime as cloudRuntime, limits } from '@/lib/cloud/config';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// The sandbox build context, served to providers so they can build the EXACT runtime image
// the gateway runs (sandbox/Dockerfile + sandbox/runner.py, straight from disk — no copies to
// drift). The worker (`python -m animica.cloud_worker build-image`) fetches this, verifies the
// sha3-256 digests, and runs `docker build`. Public: both files ship in the open-source repo;
// they contain no credentials by design.
export async function GET(_req: NextRequest) {
  try {
    const base = path.join(process.cwd(), 'sandbox');
    let dockerfile: string;
    let runner: string;
    try {
      [dockerfile, runner] = await Promise.all([
        readFile(path.join(base, 'Dockerfile'), 'utf8'),
        readFile(path.join(base, 'runner.py'), 'utf8'),
      ]);
    } catch {
      throw new ApiError(503, 'runtime_unavailable', 'the sandbox build context is not available on this node');
    }
    const sha3 = (s: string) => createHash('sha3-256').update(s, 'utf8').digest('hex');
    return publicOk({
      image: cloudRuntime.image,
      files: {
        Dockerfile: dockerfile,
        'runner.py': runner,
      },
      sha3: {
        Dockerfile: sha3(dockerfile),
        'runner.py': sha3(runner),
      },
      // The hardening contract the worker MUST apply per job (mirrors lib/cloud/sandbox.ts).
      run_contract: {
        network: 'none',
        read_only: true,
        cap_drop: 'ALL',
        no_new_privileges: true,
        user: `${cloudRuntime.uid}:${cloudRuntime.gid}`,
        pids_limit: limits.maxPids,
        tmpfs_mb: limits.maxTmpfsMb,
        max_output_bytes: limits.maxOutputBytes,
      },
    });
  } catch (e) {
    return err(e);
  }
}

export async function OPTIONS() {
  return publicPreflight();
}
