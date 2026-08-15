'use strict';
/**
 * REAL captured responses — the examples the landing page and the OpenAPI
 * document show. Every one of these was produced by running the gateway's
 * own routes on this box against the LIVE Animica node RPC
 * (http://127.0.0.1:8545/rpc) on 2026-08-15, with a mocked facilitator so
 * no money moved and a scratch database.
 *
 * Two deliberate edits, both stated where the sample is rendered:
 *   - the `payment` block (network/asset/amount/payer/settlement_tx) is
 *     REMOVED, because its settlement_tx came from the mocked facilitator
 *     and a fake Base transaction hash on a public page is a lie. The page
 *     renders that block separately from the LIVE registry instead.
 *   - the bulk-chain sample keeps one block of three, so a page stays
 *     readable; the sample says so.
 *
 * Nothing else is edited. If a product's response shape changes, recapture —
 * do not hand-patch (test/discovery.test.js asserts these still match the
 * shapes the products produce).
 */

const SAMPLES = {
  "qrng": {
    "method": "GET",
    "path": "/x402/qrng/draw?bytes=32",
    "response": {
      "product": "qrng",
      "randomness": "6950d0ba85a3c0a03b193a50e998c6c6b16b53447d0e0ccb68e2a5722ac526ea",
      "encoding": "hex",
      "bytes": 32,
      "source": {
        "name": "software-fallback",
        "vendor": "os",
        "model": "os.urandom CSPRNG",
        "is_hardware": false,
        "is_quantum": false,
        "device_path": null,
        "attested": false,
        "notes": "non-attested fallback for testing/degraded mode"
      },
      "health": {
        "passed": true,
        "min_entropy_per_byte": 7.8106
      },
      "attestation": {
        "alg": "ed25519",
        "backend": "software",
        "attested": false,
        "public_key_hex": "8a2228f4bde8f72964a7ff4aa759f24932ae1cccd9462ad94ca6e508d6b87a64",
        "digest_hex": "ae7686343e9459d0ddaabb3b80cbdb9d28599eec501ffefe4736b60355f8bf29",
        "signature_hex": "cb219cdb424f395b664076ee57cae6a1245544b53306d35860de4a689ff410dc79d27ffbcfaa927268db47da2c155e06bf0eaf2d838083f81ad1450c1a7cb904"
      },
      "verification": {
        "method": "signed-digest-attestation",
        "rules": [
          "attestation.digest_hex == sha3_256(bytes(randomness))",
          "ed25519_verify(pubkey=attestation.public_key_hex, message=raw_bytes(attestation.digest_hex), signature=attestation.signature_hex)"
        ],
        "verifier": "randomness/beacon_api/static/verify.js in the animica repo (github.com/animicaorg) — dependency-free Node module exporting sha3_256; pair with any ed25519 verifier",
        "trust_model": "signed by the serving node, not client-recomputable: you trust the node's entropy source, then verify it signed exactly these bytes. Check health.passed and attestation.attested before relying on it.",
        "attested": false
      }
    }
  },
  "random_int": {
    "method": "POST",
    "path": "/x402/random/int",
    "body": {
      "count": 3,
      "min": 1,
      "max": 6
    },
    "response": {
      "product": "random_int",
      "result": {
        "ints": [
          5,
          1,
          2
        ],
        "count": 3,
        "min": 1,
        "max": 6
      },
      "randomness": "8e8d85d9c03247ffdff7b3db1c917b7a0e97a569e8cf098071dd91cc9121ae55",
      "encoding": "hex",
      "bytes": 32,
      "source": {
        "name": "software-fallback",
        "vendor": "os",
        "model": "os.urandom CSPRNG",
        "is_hardware": false,
        "is_quantum": false,
        "device_path": null,
        "attested": false,
        "notes": "non-attested fallback for testing/degraded mode"
      },
      "health": {
        "passed": true,
        "min_entropy_per_byte": 7.8216
      },
      "attestation": {
        "alg": "ed25519",
        "backend": "software",
        "attested": false,
        "public_key_hex": "8a2228f4bde8f72964a7ff4aa759f24932ae1cccd9462ad94ca6e508d6b87a64",
        "digest_hex": "14a2636cf09e18539ab6ed62de65af350c2f6014d269b74d127fc1604c7aa68a",
        "signature_hex": "bee4d32ee31dd92b07f1eee3113ed722c3c96c5fae98c509bcca8f66a5ceaff3eb4a6cd41cd97fc32deebdeeab18b4affea467549dd4ef387ddbdae6d415e900"
      },
      "verification": {
        "method": "signed-digest-attestation",
        "rules": [
          "attestation.digest_hex == sha3_256(bytes(randomness))",
          "ed25519_verify(pubkey=attestation.public_key_hex, message=raw_bytes(attestation.digest_hex), signature=attestation.signature_hex)"
        ],
        "verifier": "randomness/beacon_api/static/verify.js in the animica repo (github.com/animicaorg) — dependency-free Node module exporting sha3_256; pair with any ed25519 verifier",
        "trust_model": "signed by the serving node, not client-recomputable: you trust the node's entropy source, then verify it signed exactly these bytes. Check health.passed and attestation.attested before relying on it.",
        "attested": false
      },
      "derivation": {
        "algorithm": "uniform-int-rejection-sampling",
        "kind": "range",
        "request_id": "",
        "domain": "animica/qrng/public/v1",
        "seed_hex": "5fb4cc0e8007c201060453ee9b7cacb38444d32d76d423c61e601117fadd3068",
        "entropy_hex": "8e8d85d9c03247ffdff7b3db1c917b7a0e97a569e8cf098071dd91cc9121ae55",
        "rules": {
          "seed": "seed = sha3_256(\"animica/qrng/public/v1\" || \"|k:\" || kind || \"|r:\" || request_id || \"|b:\" || bytes(randomness))",
          "stream": "stream = sha3_256(seed || be8(counter)) for counter = 0,1,2,… concatenated; consumed left to right, never reused",
          "uniform_int": "randbelow(n): k = floor((bitlen(n)+7)/8)+1 bytes per attempt, read big-endian as x; limit = 2^(8k); accept when x < limit - (limit mod n); value = x mod n. Rejected attempts still consume their k bytes. n <= 1 consumes nothing and returns 0. This is rejection sampling — plain \"x mod n\" would bias small values."
        },
        "steps": [
          "seed the DRNG with kind=\"range\" and your request_id over the raw randomness above",
          "for i = 0..2: ints[i] = 1 + randbelow(6)"
        ],
        "stream_bytes_consumed": 6,
        "recompute": {
          "kind": "range",
          "beacon_hex": "8e8d85d9c03247ffdff7b3db1c917b7a0e97a569e8cf098071dd91cc9121ae55",
          "round_id": 0,
          "request_id": "",
          "params": {
            "lo": 1,
            "hi": 6,
            "count": 3
          },
          "output": [
            5,
            1,
            2
          ]
        },
        "verifier": {
          "file": "randomness/beacon_api/static/verify.js (Animica repo, github.com/animicaorg) — zero-dependency, runs in Node and the browser",
          "api": "AnimicaBeacon.drngSeed(entropy, kind, request_id) + AnimicaBeacon.QDRNG reproduce the stream; AnimicaBeacon.verifyResult(derivation.recompute) === true whenever `recompute` is present",
          "note": "verify.js calls the seed material `beacon` because it was written for beacon rounds. This product is NOT a beacon round: the seed material is the raw `randomness` bytes above, which the node signed (see `verification`)."
        }
      }
    }
  },
  "bulk_chain": {
    "method": "GET",
    "path": "/x402/chain/export?from=73600&to=73602&format=json",
    "truncated": "blocks[] trimmed to the first of three for display",
    "response": {
      "meta": {
        "type": "meta",
        "product": "bulk_chain",
        "export": "blocks",
        "chain_id": 1,
        "head_height": 73635,
        "head_margin": 6,
        "from_height": 73600,
        "to_height": 73602,
        "unit": "nANM"
      },
      "blocks": [
        {
          "height": 73600,
          "hash": "0x0000000001bb6415ee925b9648e93cf3f761371bd8524618f39dd174ea9d15dd",
          "parent_hash": "0x0000000003b8249bf50aad465a666250c02b096f331ba05ac6dda2e81efa9147",
          "timestamp": 1786769588,
          "chain_id": 1,
          "theta_micro": 26004937,
          "nonce": 13769920589,
          "roots": {
            "stateRoot": "0x0000000000000000000000000000000000000000000000000000000000000000",
            "txsRoot": "0x0000000000000000000000000000000000000000000000000000000000000000",
            "receiptsRoot": "0x0000000000000000000000000000000000000000000000000000000000000000",
            "proofsRoot": "0x0000000000000000000000000000000000000000000000000000000000000000",
            "daRoot": "0x0000000000000000000000000000000000000000000000000000000000000000"
          },
          "transactions": []
        }
      ],
      "summary": {
        "type": "summary",
        "blocks": 3,
        "txs": 0,
        "truncated_reason": null,
        "next_cursor": null,
        "complete": true
      }
    }
  },
  "chain_batch_balances": {
    "method": "POST",
    "path": "/x402/chain/balances",
    "body": {
      "addresses": [
        "anim1zqpye0muk7etljd2fh7wxsh9y9027cq7dykj3de8u80s2mcnfp6qxecpunkth"
      ]
    },
    "response": {
      "product": "chain_batch_balances",
      "unit": "nANM",
      "decimals": 9,
      "count": 1,
      "unique_addresses": 1,
      "failed_lookups": 0,
      "total_balance": "150863000",
      "as_of": {
        "head_height": 73635,
        "head_hash": "0x00000000015b1e1021c5354baeac63e24fa52aff9aee92bd467653654256cdd4",
        "entry_head_min": 73635,
        "entry_head_max": 73635,
        "consistent": true
      },
      "balances": [
        {
          "address": "anim1zqpye0muk7etljd2fh7wxsh9y9027cq7dykj3de8u80s2mcnfp6qxecpunkth",
          "account_digest": "0x4cbf7cb7b2bfc9aa4dfce342e5215eaf601e692d28b727e1df056f1348740367",
          "balance": "150863000",
          "spendable_balance": "150863000",
          "exists": true
        }
      ],
      "derivation": {
        "account_digest": "bech32m anim1… payload = alg_id (2 bytes) || sha3_256(pubkey) (32 bytes); the lookup key is payload[2:34] (the 32-byte digest), which is what is sent to the node.",
        "amounts": "balances are nANM (1e-9 ANM) as exact decimal strings, normalised through BigInt from the node's confirmed_balance. Divide by 1e9 for ANM — with a decimal library, never a float.",
        "duplicates": "duplicate addresses are resolved once and answered in place; total_balance sums UNIQUE accounts only.",
        "fields": "balance = the node's confirmed_balance. spendable_balance and exists are passed through only when the node returns them; nothing here is synthesised.",
        "source": "state.getAddressBalance on the local Animica node, one batched JSON-RPC request. Note the node has an upstream read-through fallback when it believes it is behind, so a balance may come from a trusted upstream rather than local state."
      }
    }
  },
  "priority_inference": {
    "method": "POST",
    "path": "/x402/v1/chat/completions",
    "body": {
      "model": "kimi-k3",
      "messages": [
        {
          "role": "user",
          "content": "hi"
        }
      ]
    },
    "status": 503,
    "response": {
      "error": "priority_inference_unavailable",
      "serving_workers": 0,
      "required": 2,
      "enabled": false,
      "detail": "priority inference is disabled by the operator (PRIORITY_INFERENCE_ENABLED=0)"
    }
  }
};

const CAPTURED_AT = '2026-08-15';
const CAPTURE_NOTE = 'captured live on this gateway against the Animica mainnet node on 2026-08-15; the payment block is rendered separately from the live registry because the capture settled through a mocked facilitator';

module.exports = { SAMPLES, CAPTURED_AT, CAPTURE_NOTE };
