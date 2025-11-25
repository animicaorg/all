// SPDX-License-Identifier: MIT
// pragma: circom 2.1.7 (works with 2.x)

include "circomlib/circuits/bitify.circom";
include "circomlib/circuits/poseidon.circom";

/*
EmbeddingThreshold — prove a threshold on a dot product with Poseidon commitments.

Goal:
  Given vectors x, w (private) and public commitments Hx = Poseidon(x), Hw = Poseidon(w),
  and a public threshold τ, prove:
      dot(x, w) = Σ_i x[i] * w[i]  ≥  τ

Parameters:
  N   — vector length
  Bx  — bit-length bound for each x[i]          (x[i] ∈ [0, 2^Bx))
  Bw  — bit-length bound for each w[i]          (w[i] ∈ [0, 2^Bw))
  L   — bit-length bound for dot and τ          (dot, τ ∈ [0, 2^L))

Important:
  Choose L big enough to avoid modular wraparound:
      L >= Bx + Bw + ceil(log2(N))
  This file does NOT enforce the relation above at compile time; you must select safe params.

Public inputs (exposed via `public [...]` in `main`):
  - hash_x : Poseidon commitment to x (length N)
  - hash_w : Poseidon commitment to w (length N)
  - tau    : threshold τ (bounded to L bits)

Private inputs:
  - x[N], w[N] : vector elements (each range-checked to Bx/Bw bits)

Outputs:
  - dot : Σ_i x[i] * w[i]  (range-checked to L bits). Helpful for off-chain sanity.

Security notes:
  - Range constraints ensure natural integer ordering under the L-bit bound.
  - We implement a constant-time style LessThan using bit decomposition to avoid relying
    on external include paths for comparator gadgets.
*/


// Boolean LessThan over L-bit non-negative integers (a < b ? 1 : 0)
template LessThanBits(L) {
    signal input a;
    signal input b;
    signal output out; // 1 if a < b else 0

    component aBits = Num2Bits(L);
    component bBits = Num2Bits(L);
    aBits.in <== a;
    bBits.in <== b;

    // eq[i] indicates all higher bits (L-1..i+1) are equal (start true above MSB)
    // lt[i] accumulates whether a < b has already been decided in higher bits
    // Iterate MSB -> LSB
    signal eq[L + 1];
    signal lt[L + 1];

    eq[L] <== 1; // initially, no bits compared -> equal
    lt[L] <== 0; // initially, not less

    for (var i = L - 1; i >= 0; i--) {
        // ai, bi are bit i of a and b respectively
        signal ai;
        signal bi;
        ai <== aBits.out[i];
        bi <== bBits.out[i];

        // xor = ai XOR bi = ai + bi - 2*ai*bi  (works in the field for {0,1})
        signal xori;
        xori <== ai + bi - 2 * ai * bi;

        // equal up to this bit if it was equal above and this bit matches
        // eq[i] = eq[i+1] AND (NOT xor)
        eq[i] <== eq[i + 1] * (1 - xori);

        // If at this bit: ai=0, bi=1 and higher bits equal ⇒ a < b decided here
        signal lt_here;
        lt_here <== (1 - ai) * bi;

        // Once less, it stays less
        lt[i] <== lt[i + 1] + eq[i + 1] * lt_here;
    }

    // lt[0] is final decision bit
    out <== lt[0];
}


// Main embedding threshold circuit
template EmbeddingThreshold(N, Bx, Bw, L) {

    // Public inputs (declared here; exposed in `main` via `public [...]`)
    signal input hash_x;
    signal input hash_w;
    signal input tau;

    // Private inputs
    signal input x[N];
    signal input w[N];

    // Range checks for inputs
    component xRange[N];
    component wRange[N];
    for (var i = 0; i < N; i++) {
        xRange[i] = Num2Bits(Bx);
        xRange[i].in <== x[i];

        wRange[i] = Num2Bits(Bw);
        wRange[i].in <== w[i];
    }

    // Poseidon commitments for x and w
    // circomlib Poseidon takes a fixed arity; instantiate with N
    component poseX = Poseidon(N);
    component poseW = Poseidon(N);
    for (var i = 0; i < N; i++) {
        poseX.inputs[i] <== x[i];
        poseW.inputs[i] <== w[i];
    }

    // Commitments must match public hashes
    poseX.out === hash_x;
    poseW.out === hash_w;

    // Dot product accumulation
    // acc[0] = 0
    // acc[i+1] = acc[i] + x[i]*w[i]
    signal acc[N + 1];
    acc[0] <== 0;

    for (var i = 0; i < N; i++) {
        signal prod;
        prod <== x[i] * w[i];
        acc[i + 1] <== acc[i] + prod;
    }

    // Output dot and range-check to L bits
    signal output dot;
    dot <== acc[N];

    component dotRange = Num2Bits(L);
    dotRange.in <== dot;

    // Range-check tau as well
    component tauRange = Num2Bits(L);
    tauRange.in <== tau;

    // Enforce dot >= tau  ⇔  NOT (dot < tau)
    component lt = LessThanBits(L);
    lt.a <== dot;
    lt.b <== tau;

    // If lt.out == 1 then dot < tau (bad). We require lt.out == 0.
    lt.out === 0;
}


// ---- Example instantiation ----
// Adjust these defaults to your use case. Common safe choice:
// N=16 elements, Bx=16 bits, Bw=16 bits → max sum needs ~ (16+16+4)=36 bits ⇒ choose L=40.
template Main() {
    var N  = 16;
    var Bx = 16;
    var Bw = 16;
    var L  = 40;

    component E = EmbeddingThreshold(N, Bx, Bw, L);

    // Re-expose I/O for witness tooling (snarkjs)
    // Public inputs:
    signal input hash_x;
    signal input hash_w;
    signal input tau;

    // Private vectors:
    signal input x[N];
    signal input w[N];

    // Wire through
    E.hash_x <== hash_x;
    E.hash_w <== hash_w;
    E.tau    <== tau;

    for (var i = 0; i < N; i++) {
        E.x[i] <== x[i];
        E.w[i] <== w[i];
    }

    // Optional: expose dot product as output for debugging/off-chain sanity
    signal output dot;
    dot <== E.dot;
}

// Expose which inputs are public
component main = Main();
public [ main.hash_x, main.hash_w, main.tau ];
