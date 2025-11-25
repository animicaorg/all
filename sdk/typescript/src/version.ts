/**
 * SDK version & build metadata.
 * The version should match package.json.
 */

export const version = '0.1.0'

// These globals may be inlined by the bundler (see vite.config.ts define)
declare const __SDK_TARGET__: string | undefined
declare const __GIT_DESCRIBE__: string | undefined

const detectRuntimeTarget = (): 'node' | 'browser' | 'universal' => {
  if (typeof __SDK_TARGET__ !== 'undefined') return (__SDK_TARGET__ as any)
  // Fallback heuristic for unbundled TS runs
  // eslint-disable-next-line @typescript-eslint/ban-ts-comment
  // @ts-ignore
  if (typeof process !== 'undefined' && process?.release?.name === 'node') return 'node'
  if (typeof window !== 'undefined') return 'browser'
  return 'universal'
}

const detectGitDescribe = (): string | null => {
  if (typeof __GIT_DESCRIBE__ !== 'undefined') return __GIT_DESCRIBE__!
  // eslint-disable-next-line @typescript-eslint/ban-ts-comment
  // @ts-ignore
  const env = (typeof process !== 'undefined' && process?.env) ? process.env : undefined
  const val = env?.GIT_DESCRIBE || env?.npm_package_gitHead
  return val ? String(val) : null
}

export const buildMeta = {
  version,
  target: detectRuntimeTarget(),
  gitDescribe: detectGitDescribe()
}

/** Compact UA-style banner suitable for RPC headers or logs. */
export function userAgent(): string {
  const parts = [`@animica/sdk/${version}`, buildMeta.target]
  if (buildMeta.gitDescribe) parts.push(buildMeta.gitDescribe)
  return `${parts[0]} (${parts.slice(1).join('; ')})`
}
