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

  it('falls back to page origin when only location is available', () => {
    global.window = { location: { origin: 'http://site.local' } };
    expect(inferRpcUrl()).toBe('http://site.local/');
    // With no explicit WS, should convert origin and keep port/protocol
    expect(inferWsUrl()).toBe('ws://site.local/');
  });

  it('defaults to localhost when nothing else is provided', () => {
    global.window = undefined;
    expect(inferRpcUrl({})).toBe(DEFAULT_RPC);
    expect(inferWsUrl({})).toBe(DEFAULT_WS);
    expect(inferChainId({})).toBe('');
  });

  it('promotes default RPC port to default WS port when only RPC is known', () => {
    expect(inferWsUrl({ VITE_RPC_URL: 'http://127.0.0.1:8545' })).toBe('ws://127.0.0.1:8546');
  });
});
