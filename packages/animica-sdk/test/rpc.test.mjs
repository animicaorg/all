import { test } from "node:test";
import assert from "node:assert/strict";
import {
  JsonRpcClient,
  RpcError,
  RpcTransportError,
  DEFAULT_RPC_URL,
} from "../dist/esm/index.js";
import { makeFetchStub, jsonResponse } from "./helpers.mjs";

const HEAD = {
  height: 63080,
  number: 63080,
  hash: "0x0000000002b9f56caa5a13f28d6e13a930aa15da9db7231497631276d28fcb4f",
  chainId: 1,
  thetaMicro: 25617353,
  mixSeed: "0xce53af01b3a37c3d31946df2983f9ed576e85afd5a3a9033211462385765e101",
  nonce: 1329802334961679958,
  roots: {
    stateRoot: "0x" + "00".repeat(32),
    txsRoot: "0x" + "00".repeat(32),
    receiptsRoot: "0x" + "00".repeat(32),
    proofsRoot: "0x" + "00".repeat(32),
    daRoot: "0x" + "00".repeat(32),
  },
};

test("default URL is the public rpc endpoint", () => {
  const rpc = new JsonRpcClient({ fetch: makeFetchStub(() => jsonResponse({})) });
  assert.equal(rpc.url, "https://rpc.animica.org/rpc");
  assert.equal(DEFAULT_RPC_URL, "https://rpc.animica.org/rpc");
});

test("getHead shapes a correct JSON-RPC 2.0 request and unwraps result", async () => {
  const fetchStub = makeFetchStub((req) =>
    jsonResponse({ jsonrpc: "2.0", id: req.json.id, result: HEAD })
  );
  const rpc = new JsonRpcClient({ url: "http://node.test/rpc", fetch: fetchStub });
  const head = await rpc.getHead();

  assert.equal(fetchStub.calls.length, 1);
  const call = fetchStub.calls[0];
  assert.equal(call.url, "http://node.test/rpc");
  assert.equal(call.method, "POST");
  assert.equal(call.headers["content-type"], "application/json");
  assert.deepEqual(call.json, {
    jsonrpc: "2.0",
    id: call.json.id,
    method: "chain.getHead",
    params: [],
  });
  assert.equal(typeof call.json.id, "number");
  assert.equal(head.height, 63080);
  assert.equal(head.thetaMicro, 25617353);
});

test("request ids increment per call", async () => {
  const fetchStub = makeFetchStub((req) =>
    jsonResponse({ jsonrpc: "2.0", id: req.json.id, result: 1 })
  );
  const rpc = new JsonRpcClient({ fetch: fetchStub });
  await rpc.getChainId();
  await rpc.getChainId();
  assert.notEqual(fetchStub.calls[0].json.id, fetchStub.calls[1].json.id);
  assert.equal(fetchStub.calls[0].json.method, "chain.getChainId");
});

test("positional params: blocks, balance, raw tx, mempool status", async () => {
  const fetchStub = makeFetchStub((req) => {
    const { method } = req.json;
    const result =
      method === "state.getBalance"
        ? "0x2540be400"
        : method === "tx.sendRawTransaction"
          ? "0xabc123"
          : method === "mempool.getStats"
            ? { count: 2, totalBytes: 512, oldestAgeSec: 7 }
            : { number: 63000 };
    return jsonResponse({ jsonrpc: "2.0", id: req.json.id, result });
  });
  const rpc = new JsonRpcClient({ fetch: fetchStub });

  await rpc.getBlockByHeight(63000);
  assert.deepEqual(fetchStub.calls[0].json.params, [63000, false]);
  assert.equal(fetchStub.calls[0].json.method, "chain.getBlockByHeight");

  await rpc.getBlockByHash("0xdeadbeef", true);
  assert.deepEqual(fetchStub.calls[1].json.params, ["0xdeadbeef", true]);
  assert.equal(fetchStub.calls[1].json.method, "chain.getBlockByHash");

  const bal = await rpc.getBalance("anim1qqqq");
  assert.deepEqual(fetchStub.calls[2].json.params, ["anim1qqqq", "latest"]);
  assert.equal(fetchStub.calls[2].json.method, "state.getBalance");
  assert.equal(bal, 10000000000n); // 0x2540be400 as bigint

  const txh = await rpc.sendRawTransaction("0x0102");
  assert.deepEqual(fetchStub.calls[3].json.params, ["0x0102"]);
  assert.equal(fetchStub.calls[3].json.method, "tx.sendRawTransaction");
  assert.equal(txh, "0xabc123");

  const mp = await rpc.getMempoolStats();
  assert.equal(fetchStub.calls[4].json.method, "mempool.getStats");
  assert.deepEqual(fetchStub.calls[4].json.params, []);
  assert.equal(mp.count, 2);
});

test("getTotalSupply returns hex quantity + addressCount", async () => {
  const fetchStub = makeFetchStub((req) =>
    jsonResponse({
      jsonrpc: "2.0",
      id: req.json.id,
      result: { totalSupply: "0x17643c1e346c2bd", addressCount: 128 },
    })
  );
  const rpc = new JsonRpcClient({ fetch: fetchStub });
  const s = await rpc.getTotalSupply();
  assert.equal(fetchStub.calls[0].json.method, "state.getTotalSupply");
  assert.equal(s.addressCount, 128);
  assert.equal(BigInt(s.totalSupply), 0x17643c1e346c2bdn);
  assert.equal(BigInt(s.totalSupply), 105346141310599869n); // ≈105.35M ANM at 9 decimals
});

test("JSON-RPC error objects raise RpcError with code/data", async () => {
  const fetchStub = makeFetchStub((req) =>
    jsonResponse({
      jsonrpc: "2.0",
      id: req.json.id,
      error: { code: -32010, message: "InvalidTransaction", data: { x: 1 } },
    })
  );
  const rpc = new JsonRpcClient({ fetch: fetchStub });
  await assert.rejects(
    () => rpc.sendRawTransaction("0x00"),
    (err) => {
      assert.ok(err instanceof RpcError);
      assert.equal(err.code, -32010);
      assert.equal(err.method, "tx.sendRawTransaction");
      assert.deepEqual(err.data, { x: 1 });
      return true;
    }
  );
});

test("HTTP failures raise RpcTransportError with status", async () => {
  const fetchStub = makeFetchStub(() => jsonResponse({ oops: true }, 503));
  const rpc = new JsonRpcClient({ fetch: fetchStub });
  await assert.rejects(
    () => rpc.getHead(),
    (err) => {
      assert.ok(err instanceof RpcTransportError);
      assert.equal(err.status, 503);
      return true;
    }
  );
});

test("generic call() passes arbitrary method + params through", async () => {
  const fetchStub = makeFetchStub((req) =>
    jsonResponse({ jsonrpc: "2.0", id: req.json.id, result: { ok: true } })
  );
  const rpc = new JsonRpcClient({ fetch: fetchStub });
  const out = await rpc.call("chain.getNetworkHashrate", [720]);
  assert.deepEqual(fetchStub.calls[0].json.method, "chain.getNetworkHashrate");
  assert.deepEqual(fetchStub.calls[0].json.params, [720]);
  assert.deepEqual(out, { ok: true });
});
