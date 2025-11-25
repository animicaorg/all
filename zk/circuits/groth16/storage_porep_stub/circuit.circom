// SPDX-License-Identifier: MIT
// Storage PoRep (stub) — commitment + ticket check
// This is a minimal Circom 2.x circuit intended for tests/demo. It does NOT
// implement full PoRep. It only:
//  1) Recomputes a Poseidon commitment to a fixed-size preimage (private)
//  2) Checks a "ticket" = Poseidon(DST, Cdata, minerId, challenge, nonce)
//  3) Range-checks nonce to 64 bits
//
// Public signals (order matters!):
//  - Cdata      : Poseidon commitment to preimage (recomputed in-circuit)
//  - minerId    : public miner identifier (field element domain)
//  - challenge  : public challenge (e.g., epoch/round-specific randomness)
//  - ticket     : expected ticket = Poseidon("porep:ticket", Cdata, minerId, challenge, nonce)
//
// Private inputs:
//  - preimage[N]: the data that is committed by Cdata (N field elements)
//  - nonce      : 64-bit search nonce
//
// Domain Separation Tags (numeric constants):
//  - DST_DATA   : "porep:data"
//  - DST_TICKET : "porep:ticket"
//
// Notes:
//  - N is a compile-time template parameter (fixed arity).
//  - This circuit keeps everything in the field; if you need byte-level hashing,
//    do the byte->field packing off-circuit and commit to those words here.

pragma circom 2.1.4;

include "circomlib/poseidon.circom";
include "circomlib/bitify.circom";

var DST_DATA   = 1337; // stand-in for "porep:data" DST
var DST_TICKET = 4242; // stand-in for "porep:ticket" DST

template StoragePoRepStub(N) {
    // ---------- Inputs ----------
    signal input Cdata;        // public
    signal input minerId;      // public
    signal input challenge;    // public
    signal input ticket;       // public

    signal input private nonce;            // private (range-checked to 64 bits)
    signal input private preimage[N];      // private commitment preimage

    // ---------- Commitment recomputation ----------
    // c_calc = Poseidon(DST_DATA, N, preimage[0], ..., preimage[N-1])
    component hData = Poseidon(N + 2);
    hData.inputs[0] <== DST_DATA;
    hData.inputs[1] <== N;
    for (var i = 0; i < N; i++) {
        hData.inputs[i + 2] <== preimage[i];
    }
    // Constrain equality with the public commitment
    Cdata === hData.out;

    // ---------- Nonce range check (64-bit) ----------
    component nbits = Num2Bits(64);
    nbits.in <== nonce;

    // ---------- Ticket recomputation ----------
    // t_calc = Poseidon(DST_TICKET, Cdata, minerId, challenge, nonce)
    component hTicket = Poseidon(5);
    hTicket.inputs[0] <== DST_TICKET;
    hTicket.inputs[1] <== Cdata;
    hTicket.inputs[2] <== minerId;
    hTicket.inputs[3] <== challenge;
    hTicket.inputs[4] <== nonce;

    // Constrain equality with the public ticket
    ticket === hTicket.out;
}

// Default main with a modest preimage arity suitable for tests.
// Adjust N at compile time if you need a different arity.
component main = StoragePoRepStub(8);
