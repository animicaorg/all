import { defineConfig } from 'vite'
import { resolve } from 'path'

/**
 * We build a multi-entry library and preserve the source folder structure in /dist
 * so deep imports like `@animica/sdk/rpc/http` work. The package.json should
 * expose subpath exports (e.g. "./rpc/http": "./dist/rpc/http.js") or a catch-all
 * pattern ("./*": "./dist/*") for strict Node ESM resolution.
 *
 * Type definitions are emitted by `tsc` in the build script.
 */

const entries: Record<string, string> = {
  // root
  'index': resolve(__dirname, 'src/index.ts'),
  'version': resolve(__dirname, 'src/version.ts'),
  'errors': resolve(__dirname, 'src/errors.ts'),

  // rpc
  'rpc/http': resolve(__dirname, 'src/rpc/http.ts'),
  'rpc/ws': resolve(__dirname, 'src/rpc/ws.ts'),

  // wallet
  'wallet/index': resolve(__dirname, 'src/wallet/index.ts'),
  'wallet/mnemonic': resolve(__dirname, 'src/wallet/mnemonic.ts'),
  'wallet/keystore': resolve(__dirname, 'src/wallet/keystore.ts'),
  'wallet/signer': resolve(__dirname, 'src/wallet/signer.ts'),

  // address
  'address': resolve(__dirname, 'src/address.ts'),

  // tx
  'tx/build': resolve(__dirname, 'src/tx/build.ts'),
  'tx/encode': resolve(__dirname, 'src/tx/encode.ts'),
  'tx/send': resolve(__dirname, 'src/tx/send.ts'),

  // contracts
  'contracts/index': resolve(__dirname, 'src/contracts/index.ts'),
  'contracts/client': resolve(__dirname, 'src/contracts/client.ts'),
  'contracts/deployer': resolve(__dirname, 'src/contracts/deployer.ts'),
  'contracts/events': resolve(__dirname, 'src/contracts/events.ts'),
  'contracts/codegen': resolve(__dirname, 'src/contracts/codegen.ts'),

  // DA / AICF / Randomness / Light client / Proofs
  'da/client': resolve(__dirname, 'src/da/client.ts'),
  'aicf/client': resolve(__dirname, 'src/aicf/client.ts'),
  'randomness/client': resolve(__dirname, 'src/randomness/client.ts'),
  'light_client/verify': resolve(__dirname, 'src/light_client/verify.ts'),
  'proofs/hashshare': resolve(__dirname, 'src/proofs/hashshare.ts'),
  'proofs/ai': resolve(__dirname, 'src/proofs/ai.ts'),
  'proofs/quantum': resolve(__dirname, 'src/proofs/quantum.ts'),

  // shared utils
  'utils/bytes': resolve(__dirname, 'src/utils/bytes.ts'),
  'utils/hash': resolve(__dirname, 'src/utils/hash.ts'),
  'utils/cbor': resolve(__dirname, 'src/utils/cbor.ts'),
  'utils/bech32': resolve(__dirname, 'src/utils/bech32.ts'),
  'utils/retry': resolve(__dirname, 'src/utils/retry.ts')
}

export default defineConfig({
  build: {
    // We drive Rollup directly for a multi-entry / preserveModules layout
    lib: false,
    sourcemap: true,
    target: 'es2022',
    minify: false,
    rollupOptions: {
      input: entries,
      // Externalize node built-ins and optional deps that should not be bundled
      external: [
        'fs',
        'path',
        'crypto',
        'buffer',
        'events',
        'stream',
        'util',
        'zlib',
        // If you use ws or other optional deps, keep them external:
        'ws'
      ],
      output: [
        // ESM output mirrors src/ structure
        {
          dir: 'dist',
          format: 'es',
          entryFileNames: (chunk) => {
            // keep "index" at root as index.js, others keep their path (e.g., rpc/http.js)
            return chunk.name === 'index' ? 'index.js' : `${chunk.name}.js`
          },
          preserveModules: true,
          preserveModulesRoot: 'src',
          sourcemap: true
        },
        // CJS output mirrors src/ structure
        {
          dir: 'dist',
          format: 'cjs',
          exports: 'named',
          entryFileNames: (chunk) => {
            return chunk.name === 'index' ? 'index.cjs' : `${chunk.name}.cjs`
          },
          preserveModules: true,
          preserveModulesRoot: 'src',
          sourcemap: true
        }
      ],
      treeshake: {
        moduleSideEffects: false,
        propertyReadSideEffects: false,
        unknownGlobalSideEffects: false
      }
    }
  },
  define: {
    __SDK_TARGET__: JSON.stringify(process.env.SDK_TARGET || 'universal') // "node" | "browser" | "universal"
  }
})
