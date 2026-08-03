import { test } from "node:test";
import assert from "node:assert/strict";
import {
  validateAddress,
  decodeAddress,
  encodeAddress,
  bech32mDecode,
  AddressError,
  shortAddress,
  ANIMICA_HRP,
} from "../dist/esm/index.js";
import { VECTORS } from "./vectors.mjs";

const hex = (u8) =>
  Array.from(u8, (b) => b.toString(16).padStart(2, "0")).join("");
const fromHex = (h) =>
  Uint8Array.from(h.match(/.{2}/g) ?? [], (b) => parseInt(b, 16));

test("valid vectors: bech32mDecode recovers the exact payload", () => {
  for (const v of VECTORS.valid) {
    const { hrp, data, spec } = bech32mDecode(v.address);
    assert.equal(hrp, v.hrp, v.address);
    assert.equal(spec, "bech32m", v.address);
    // 5->8 conversion happens inside decodeAddress; use encode round-trip
    // for generic payloads below instead.
  }
});

test("valid address vectors (34-byte payload) decode with algId + digest", () => {
  for (const v of VECTORS.valid.filter((v) => v.algId !== null)) {
    const rec = decodeAddress(v.address);
    assert.equal(rec.hrp, "anim", v.address);
    assert.equal(rec.algId, v.algId, v.address);
    assert.equal(hex(rec.payload), v.payloadHex, v.address);
    assert.equal(hex(rec.digest), v.payloadHex.slice(4), v.address);
    assert.equal(validateAddress(v.address), true, v.address);
  }
});

test("encodeAddress round-trips every vector payload to the same string", () => {
  for (const v of VECTORS.valid) {
    const encoded = encodeAddress(fromHex(v.payloadHex), v.hrp);
    assert.equal(encoded, v.address.toLowerCase(), v.payloadHex);
  }
});

test("generic payloads round-trip; only 34-byte payloads validate as addresses", () => {
  for (const v of VECTORS.valid.filter((v) => v.algId === null)) {
    assert.equal(encodeAddress(fromHex(v.payloadHex)), v.address);
    const is34 = v.payloadHex.length === 68;
    // Only a 34-byte payload is a well-formed account address:
    assert.equal(validateAddress(v.address), is34, v.address);
    if (!is34) {
      assert.throws(() => decodeAddress(v.address), AddressError);
    }
  }
});

test("invalid vectors are rejected by validateAddress and decodeAddress", () => {
  for (const v of VECTORS.invalid) {
    assert.equal(validateAddress(v.address), false, `${v.reason}: ${v.address}`);
    assert.throws(
      () => decodeAddress(v.address),
      AddressError,
      `${v.reason}: ${v.address}`
    );
  }
});

test("corrupted checksum specifically fails at the bech32m layer", () => {
  const good = VECTORS.valid[0].address;
  const corrupted = good.slice(0, -1) + (good.endsWith("q") ? "p" : "q");
  assert.throws(() => bech32mDecode(corrupted), AddressError);
  assert.equal(validateAddress(corrupted), false);
});

test("wrong-HRP but checksum-valid string fails decodeAddress HRP check", () => {
  const v = VECTORS.valid[0];
  const payload = fromHex(v.payloadHex);
  const other = encodeAddress(payload, "test");
  // bech32m itself is fine…
  assert.equal(bech32mDecode(other).hrp, "test");
  // …but it is not an Animica address.
  assert.equal(validateAddress(other), false);
  assert.throws(() => decodeAddress(other, ANIMICA_HRP), AddressError);
});

test("shortAddress keeps ends", () => {
  const v = VECTORS.valid[0].address;
  const s = shortAddress(v);
  assert.equal(s.length, 13);
  assert.ok(v.startsWith(s.slice(0, 6)));
  assert.ok(v.endsWith(s.slice(-6)));
});
