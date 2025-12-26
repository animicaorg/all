import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import { inferChainId, inferRpcUrl, inferWsUrl, DEFAULT_RPC, DEFAULT_WS } from './env';

declare const global: any;

const originalWindow = global.window;

beforeEach(() => {
  global.window = undefined;
});

afterEach(() => {
  global.window = originalWindow;
});

describe('env helpers', () => {
  it('prefers explicit env RPC/WS and chain id', () => {
    const env = { VITE_RPC_URL: 'http://rpc.env', VITE_RPC_WS: 'ws://ws.env', VITE_CHAIN_ID: 42 };
    expect(inferRpcUrl(env)).toBe('http://rpc.env');
    expect(inferWsUrl(env)).toBe('ws://ws.env');
    expect(inferChainId(env)).toBe('42');
  });

  it('uses injected window globals when env is absent', () => {
    global.window = { __ANIMICA_RPC_URL__: 'http://rpc.injected', __ANIMICA_WS_URL__: 'ws://ws.injected' };
    expect(inferRpcUrl()).toBe('http://rpc.injected');
    expect(inferWsUrl()).toBe('ws://ws.injected');
  });

  it('uses the public Animica RPC by default even with a window location', () => {
    global.window = { location: { origin: 'http://site.local' } };
    expect(inferRpcUrl()).toBe(DEFAULT_RPC);
    expect(inferWsUrl()).toBe('ws://127.0.0.1:8546/ws');
  });

  it('defaults to the public Animica endpoints when nothing else is provided', () => {
    global.window = undefined;
    expect(inferRpcUrl({})).toBe(DEFAULT_RPC);
    expect(inferWsUrl({})).toBe(DEFAULT_WS);
    expect(inferChainId({})).toBe('');
  });

  it('promotes default RPC port to default WS port when only RPC is known', () => {
    expect(inferWsUrl({ VITE_RPC_URL: 'http://127.0.0.1:8545' })).toBe('ws://127.0.0.1:8546');
  });

  it('supports VITE_RPC_HTTP as an alternative to VITE_RPC_URL', () => {
    const env = { VITE_RPC_HTTP: 'http://rpc.alt' };
    expect(inferRpcUrl(env)).toBe('http://rpc.alt');
  });

  it('prefers VITE_RPC_URL over VITE_RPC_HTTP when both are present', () => {
    const env = { VITE_RPC_URL: 'http://primary', VITE_RPC_HTTP: 'http://fallback' };
    expect(inferRpcUrl(env)).toBe('http://primary');
  });

  it('converts hex chain IDs to decimal strings', () => {
    expect(inferChainId({ VITE_CHAIN_ID: '0x1' })).toBe('1');
    expect(inferChainId({ VITE_CHAIN_ID: '0x539' })).toBe('1337');
  });

  it('normalizes legacy mainnet chain IDs to the canonical value', () => {
    expect(inferChainId({ VITE_CHAIN_ID: '0xa11ca' })).toBe('1');
    expect(inferChainId({ VITE_CHAIN_ID: '659658' })).toBe('1');
    expect(inferChainId({ VITE_CHAIN_ID: '659914' })).toBe('1');
  });

  it('handles decimal chain IDs as-is', () => {
    expect(inferChainId({ VITE_CHAIN_ID: '2' })).toBe('2');
    expect(inferChainId({ VITE_CHAIN_ID: 1337 })).toBe('1337');
  });

  it('handles invalid hex chain IDs gracefully', () => {
    expect(inferChainId({ VITE_CHAIN_ID: '0xInvalid' })).toBe('0xInvalid');
  });
});
