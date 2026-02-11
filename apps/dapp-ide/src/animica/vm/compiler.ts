/**
 * VM Compiler Integration with studio-wasm
 * Falls back to mock compiler when studio-wasm is not available
 */

export interface CompileParams {
  source: string;
  manifest: any;
  withBytes?: boolean;
}

export interface CompileResult {
  ir: Uint8Array;
  codeHash?: string;
  codeSize?: number;
  abi?: any;
  manifest?: any;
  diagnostics?: string[];
  gasUpperBound?: number;
  ok?: boolean;
}

export interface SimulateCallParams {
  contractAddress: string;
  method: string;
  args: any[];
  from?: string;
}

export interface SimulateDeployParams {
  code: Uint8Array;
  manifest?: any;
  args?: any[];
  from?: string;
}

/**
 * Mock compiler for when studio-wasm is not available
 */
async function mockCompileSource(params: CompileParams): Promise<CompileResult> {
  console.log("Using mock compiler (studio-wasm not available)");
  
  // Simulate compilation delay
  await new Promise((resolve) => setTimeout(resolve, 800));
  
  // Generate deterministic mock hash from source
  const encoder = new TextEncoder();
  const data = encoder.encode(params.source);
  const hashBuffer = await crypto.subtle.digest("SHA-256", data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  const codeHash = "0x" + hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
  
  // Mock IR
  const ir = new Uint8Array([
    0x41, 0x4e, 0x49, 0x4d, // ANIM magic
    0x01, 0x00, 0x00, 0x00, // version
    ...data.slice(0, Math.min(100, data.length))
  ]);
  
  // Extract function names from Python source (very basic)
  const functionPattern = /def\s+(\w+)\s*\([^)]*\)/g;
  const functions: any[] = [];
  let match;
  
  while ((match = functionPattern.exec(params.source)) !== null) {
    const funcName = match[1];
    if (!funcName.startsWith('_')) { // Public functions
      functions.push({
        name: funcName,
        kind: funcName === 'deploy' ? 'deploy' : 'call',
        stateMutability: funcName.startsWith('get') || funcName.startsWith('view') ? 'view' : 'nonpayable',
        inputs: [],
        outputs: [],
      });
    }
  }
  
  const abi = {
    abiVersion: "1.0.0",
    encoding: "animica-abi/1",
    contract: {
      name: params.manifest?.package?.name || "Contract",
      capabilities: [],
    },
    functions: functions.length > 0 ? functions : [
      {
        name: "deploy",
        kind: "deploy",
        stateMutability: "nonpayable",
        inputs: [],
        outputs: [],
      }
    ],
    events: [],
    errors: [],
  };
  
  return {
    ir,
    codeHash,
    codeSize: ir.length,
    abi,
    manifest: params.manifest,
    diagnostics: [
      "✓ Mock compilation successful",
      `  Code hash: ${codeHash.slice(0, 20)}...`,
      `  Size: ${ir.length} bytes`,
      `  Functions: ${functions.length}`,
      "",
      "⚠ Note: Using mock compiler. Real studio-wasm compilation not available.",
    ],
    gasUpperBound: 50000 + params.source.length * 10,
    ok: true,
  };
}

/**
 * Compile Python source code to IR using studio-wasm (or mock)
 */
export async function compileSource(params: CompileParams): Promise<CompileResult> {
  // Always use mock for now since studio-wasm is not built
  return mockCompileSource(params);
  
  // TODO: Enable real compilation when studio-wasm is available
  // try {
  //   const studioWasm = await import("@animica/studio-wasm");
  //   const result = await studioWasm.compileSource({
  //     source: params.source,
  //     manifest: params.manifest,
  //     withBytes: params.withBytes !== false,
  //   });
  //   return { ...result, ok: result.ok !== false };
  // } catch (error) {
  //   return mockCompileSource(params);
  // }
}

/**
 * Compile IR bytes to artifact
 */
export async function compileIR(params: {
  ir: Uint8Array | string | any;
  manifest?: any;
  withBytes?: boolean;
}): Promise<CompileResult> {
  throw new Error("compileIR requires studio-wasm (not available)");
}

/**
 * Link code hash into manifest
 */
export function linkManifest(manifest: any, codeHash: string, extras?: any): any {
  // Simple merge
  return {
    ...manifest,
    codeHash,
    code_hash: codeHash,
    ...(extras || {}),
  };
}

/**
 * Simulate contract execution locally (if available)
 */
export async function simulateCall(params: SimulateCallParams): Promise<any> {
  console.log("Simulating call (mock):", params);
  
  // Mock simulation
  return {
    result: null,
    gasUsed: 21000,
    logs: [],
    events: [],
  };
}

/**
 * Simulate contract deployment locally (if available)
 */
export async function simulateDeploy(params: SimulateDeployParams): Promise<any> {
  console.log("Simulating deploy (mock):", params);
  
  // Mock simulation
  return {
    contractAddress: "0x" + Array.from({ length: 40 }, () =>
      Math.floor(Math.random() * 16).toString(16)
    ).join(""),
    gasUsed: 100000,
    logs: [],
  };
}
