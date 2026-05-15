import { test } from "node:test";
import assert from "node:assert/strict";
import { animToBaseUnits, baseUnitsToAnim, ONE_ANIM } from "./units.js";

test("1 ANIMICA = 10^9 base units", () => {
  assert.equal(animToBaseUnits("1"), ONE_ANIM);
});

test("fractional ANIMICA rounds-down to 9 decimals", () => {
  assert.equal(animToBaseUnits("0.5"), 500_000_000n);
  assert.equal(animToBaseUnits("0.000000001"), 1n);
});

test("rejects non-numeric input", () => {
  assert.throws(() => animToBaseUnits("not-a-number"));
  assert.throws(() => animToBaseUnits("-1"));
  assert.throws(() => animToBaseUnits("0.0000000001")); // 10 decimals
});

test("baseUnitsToAnim round-trips", () => {
  for (const v of ["1", "0.5", "100", "1000", "0.000000001"]) {
    assert.equal(baseUnitsToAnim(animToBaseUnits(v)), v);
  }
});
