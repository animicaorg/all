/**
 * Poseidon hash circuit (PLONK/KZG friendly)
 * ------------------------------------------------------------
 * - Proving system: agnostic at circuit level; intended for PLONK with KZG
 *   commitments when compiled/verified with snarkjs (or compatible tools).
 * - Hash function: Poseidon over the BN254 scalar field (Fr).
 * - Inputs: N field elements (compile-time constant).
 * - Output: single field element = Poseidon(N)(inputs[0..N-1]).
 *
 * Usage notes
 * -----------
 * - Requires circomlib (v2) available on include path:
 *     include "circomlib/circuits/poseidon.circom";
 * - Compile (example, N=3 main):
 *     circom zk/circuits/plonk_kzg/poseidon_hash/circuit.circom \
 *       --r1cs --wasm --sym -o build/poseidon_hash
 * - Setup (PLONK + KZG) and prove with snarkjs:
 *     snarkjs plonk setup build/poseidon_hash/circuit.r1cs powersOfTau28_hez_final.ptau vk.zkey
 *     snarkjs zkey export verificationkey vk.zkey vk.json
 *     # Create input.json with { "in": ["...","...","..."] }
 *     node build/poseidon_hash/circuit_js/generate_witness.js \
 *       build/poseidon_hash/circuit_js/circuit.wasm input.json witness.wtns
 *     snarkjs plonk prove vk.zkey witness.wtns proof.json public.json
 *     snarkjs plonk verify vk.json public.json proof.json
 *
 * Security & constraints
 * ----------------------
 * - Poseidon is ARX-like over Fr; no range checks required for inputs here
 *   (they are field elements modulo BN254 prime during witness gen).
 * - Keep N small (<= 6) for minimal constraints; for large inputs prefer
 *   a tree hash (provided below) that uses Poseidon(2) in a Merkle-like fold.
 */

pragma circom 2.0.0;

include "circomlib/circuits/poseidon.circom";

/**
 * PoseidonHash(N)
 * ---------------
 * Thin wrapper around circomlib's Poseidon(N) template that exposes a
 * straight-through interface (in[N] -> out).
 */
template PoseidonHash(N) {
    assert(N > 0, "PoseidonHash: N must be > 0");

    signal input in[N];
    signal output out;

    component H = Poseidon(N);
    for (var i = 0; i < N; i++) {
        H.inputs[i] <== in[i];
    }

    out <== H.out;
}

/**
 * PoseidonTree(LEAVES)
 * --------------------
 * Balanced binary fold using Poseidon(2).
 * Useful when you need a single digest for many items with better constraint
 * efficiency than a single large-arity sponge call.
 *
 * - LEAVES must be a power of two.
 * - inputs: leaf[LEAVES]
 * - output: root
 */
template PoseidonTree(LEAVES) {
    assert(LEAVES > 1, "PoseidonTree: need at least 2 leaves");
    assert(((LEAVES & (LEAVES - 1)) == 0), "PoseidonTree: LEAVES must be power-of-two");

    signal input leaf[LEAVES];
    signal output root;

    var levelSize = LEAVES;

    // We iteratively fold pairs at each level until one root remains.
    signal level[32][];  // up to 2^32 guard; actual size is determined below

    // Initialize level 0 with input leaves
    for (var i = 0; i < LEAVES; i++) {
        level[0].push();
        level[0][i] <== leaf[i];
    }

    var lvl = 0;
    while (levelSize > 1) {
        var nextSize = levelSize / 2;
        // Create next level signals
        for (var j = 0; j < nextSize; j++) {
            level[lvl + 1].push();
        }

        // Hash pairs with Poseidon(2)
        for (var k = 0; k < nextSize; k++) {
            component H = Poseidon(2);
            H.inputs[0] <== level[lvl][2 * k];
            H.inputs[1] <== level[lvl][2 * k + 1];
            level[lvl + 1][k] <== H.out;
        }

        levelSize = nextSize;
        lvl += 1;
    }

    root <== level[lvl][0];
}

/**
 * Default entry point:
 * - 3-input Poseidon hash for convenient testing and simple examples.
 *   Adjust N or replace `main` binding during your own builds as needed.
 */
component main = PoseidonHash(3);
