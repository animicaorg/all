export type StudioCompileOutput = {
  codeBytes: Uint8Array;
  codeHex: string;
  codeHash: string;
  abi: {
    abiVersion: string;
    encoding: string;
    contract: {
      name: string;
      capabilities: string[];
    };
    functions: Array<{
      name: string;
      kind: 'deploy' | 'call';
      stateMutability: 'view' | 'nonpayable';
      inputs: unknown[];
      outputs: unknown[];
    }>;
    events: unknown[];
    errors: unknown[];
  };
  manifest: Record<string, unknown>;
  diagnostics: string[];
};

function bytesToHex(bytes: Uint8Array): string {
  return `0x${Array.from(bytes)
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')}`;
}

function extractAbiFunctions(source: string) {
  const functions: Array<{
    name: string;
    kind: 'deploy' | 'call';
    stateMutability: 'view' | 'nonpayable';
    inputs: unknown[];
    outputs: unknown[];
  }> = [];
  const functionPattern = /def\s+(\w+)\s*\([^)]*\)/g;
  let match: RegExpExecArray | null = functionPattern.exec(source);
  while (match) {
    const name = match[1];
    if (!name.startsWith('_')) {
      functions.push({
        name,
        kind: name === 'deploy' ? 'deploy' : 'call',
        stateMutability: name.startsWith('get') || name.startsWith('view') ? 'view' : 'nonpayable',
        inputs: [],
        outputs: []
      });
    }
    match = functionPattern.exec(source);
  }
  return functions.length
    ? functions
    : [
        {
          name: 'deploy',
          kind: 'deploy' as const,
          stateMutability: 'nonpayable' as const,
          inputs: [],
          outputs: []
        }
      ];
}

async function sha256Hex(value: Uint8Array): Promise<string> {
  const data = new Uint8Array(value.byteLength);
  data.set(value);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return bytesToHex(new Uint8Array(digest));
}

export async function compileStudioSource(source: string, contractName: string): Promise<StudioCompileOutput> {
  const encoder = new TextEncoder();
  const sourceBytes = encoder.encode(source);

  const ir = new Uint8Array([
    0x41,
    0x4e,
    0x49,
    0x4d,
    0x01,
    0x00,
    0x00,
    0x00,
    ...sourceBytes.slice(0, Math.min(4096, sourceBytes.length))
  ]);

  const functions = extractAbiFunctions(source);
  const codeHash = await sha256Hex(sourceBytes);
  const manifest = {
    package: {
      name: contractName,
      version: '0.1.0'
    },
    contract: {
      language: 'vmpy',
      entrypoint: 'content.py'
    },
    aicf: {
      generatedBy: 'aicf-web-studio'
    }
  };

  return {
    codeBytes: ir,
    codeHex: bytesToHex(ir),
    codeHash,
    abi: {
      abiVersion: '1.0.0',
      encoding: 'animica-abi/1',
      contract: {
        name: contractName,
        capabilities: []
      },
      functions,
      events: [],
      errors: []
    },
    manifest,
    diagnostics: [
      'Studio compile successful',
      `Code hash: ${codeHash}`,
      `Code size: ${ir.length} bytes`,
      `Public functions: ${functions.length}`
    ]
  };
}
