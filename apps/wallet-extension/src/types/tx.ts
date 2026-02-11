// Core transaction types from discovery doc

export interface UnsignedTxV2 {
  v: 2;
  chainId: number;
  from: Uint8Array;
  gas: {
    price: number;
    limit: number;
  };
  payload: {
    t: TxKind;
    v: TxPayload;
  };
  accessList?: AccessListEntry[];
  validAfter: number;
  validUntil: number;
  salt: Uint8Array;
  forkId?: number;
}

export interface UnsignedTxV1 {
  v: 1;
  chainId: number;
  from: Uint8Array;
  gas: {
    price: number;
    limit: number;
  };
  payload: {
    t: TxKind;
    v: TxPayload;
  };
  accessList?: AccessListEntry[];
  nonce: number;
}

export type UnsignedTx = UnsignedTxV2 | UnsignedTxV1;

export enum TxKind {
  TRANSFER = 0,
  DEPLOY = 1,
  CALL = 2,
  COINBASE = 3,
}

export interface TxTransfer {
  to: Uint8Array;
  amount: number;
  data?: Uint8Array;
}

export interface TxDeploy {
  code: Uint8Array;
  manifest: Uint8Array;
}

export interface TxCall {
  to: Uint8Array;
  data: Uint8Array;
}

export type TxPayload = TxTransfer | TxDeploy | TxCall;

export interface AccessListEntry {
  address: Uint8Array;
  storageKeys: Uint8Array[];
}

export interface PqSignature {
  alg: number;
  pubkey: Uint8Array;
  sig: Uint8Array;
}

export interface SignedTx {
  tx: UnsignedTx;
  sigs: PqSignature[];
}

export enum TxStatus {
  CREATED_LOCAL = 'created_local',
  SUBMITTED = 'submitted',
  MEMPOOL_ACCEPTED = 'mempool_accepted',
  INCLUDED = 'included',
  CONFIRMED = 'confirmed',
  DROPPED = 'dropped',
  REORGED_OUT = 'reorged_out',
  NOT_FOUND = 'not_found',
}

export interface PendingTx {
  txid: string;
  unsignedHash: string;
  signedTx: SignedTx;
  status: TxStatus;
  submittedAt: number;
  lastCheckedAt?: number;
  blockHeight?: number;
  confirmations?: number;
  error?: string;
}
