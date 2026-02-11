/**
 * Common Types for Animica Dapp IDE
 */

// Project structure
export interface Project {
  id: string;
  name: string;
  description: string;
  createdAt: number;
  updatedAt: number;
  files: ProjectFile[];
  manifest?: Manifest;
  compiledArtifact?: CompiledArtifact;
}

export interface ProjectFile {
  path: string;
  content: string;
  type: "python" | "json" | "text";
  lastModified: number;
}

// Manifest structure (from spec/manifest.schema.json)
export interface Manifest {
  manifestVersion: string;
  encoding: "animica-manifest/1";
  package: {
    name: string;
    version: string;
    description?: string;
    authors?: string[];
    license?: string;
  };
  target: {
    vm: "python";
    vmVersion: string;
    abiVersion: string;
  };
  entrypoint: string;
  code: {
    source?: SourceFile[];
    ir?: IRBlob[];
    toolchain?: string;
  };
  abi?: ABI;
  capabilities?: {
    required?: string[];
    optional?: string[];
    resourceLimits?: Record<string, any>;
  };
  integrity?: {
    codeHash?: string;
    abiHash?: string;
    manifestHash?: string;
    signatures?: any[];
  };
}

export interface SourceFile {
  path: string;
  sha3_256: string;
  size: number;
  mime: string;
}

export interface IRBlob {
  module: string;
  bytes: string;
  sha3_256: string;
  size: number;
}

// ABI structure (from spec/abi.schema.json)
export interface ABI {
  abiVersion: string;
  encoding: "animica-abi/1";
  contract: {
    name: string;
    capabilities?: string[];
  };
  functions: ABIFunction[];
  events?: ABIEvent[];
  errors?: ABIError[];
}

export interface ABIFunction {
  name: string;
  kind: "deploy" | "call";
  stateMutability: "view" | "nonpayable" | "payable";
  inputs: ABIParameter[];
  outputs: ABIParameter[];
}

export interface ABIParameter {
  name: string;
  type: string;
  components?: ABIParameter[];
}

export interface ABIEvent {
  name: string;
  inputs: ABIParameter[];
  anonymous?: boolean;
}

export interface ABIError {
  name: string;
  inputs: ABIParameter[];
}

// Compiled artifact
export interface CompiledArtifact {
  ir: Uint8Array;
  codeHash: string;
  abi: ABI;
  manifest: Manifest;
  compiledAt: number;
  diagnostics?: string[];
}

// Network configuration
export interface NetworkConfig {
  name: string;
  chainId: number;
  rpcUrl: string;
  wsUrl?: string;
}

// Wallet state
export interface WalletState {
  connected: boolean;
  account: string | null;
  chainId: number | null;
}
