/*
 * Animica VM Core IR — Small-Step Semantics in K
 * ------------------------------------------------
 * This K definition specifies a small, deterministic stack-machine IR used by
 * Animica’s Python-VM backend. It is intentionally minimal yet expressive
 * enough to model:
 *   - Pure arithmetic/logic on unbounded integers
 *   - A tiny memory and persistent storage
 *   - Structured control flow (JUMP/JUMPI with relative skips)
 *   - Event emission (EMIT)
 *   - Gas accounting and halting (HALT/RETURN/REVERT/OOG)
 *   - Deterministic “syscalls” (BLOB_PIN/AI_ENQ/Q_ENQ/RANDOM) summarized as
 *     uninterpreted, *pure* functions on their inputs, i.e., no I/O here.
 *
 * Design choices for robustness in proofs:
 *   • No implicit non-determinism. Every rewrite is total or reverts.
 *   • Bytes are modeled as Int with “value = big-endian integer” convention.
 *     (You can refine to a dedicated BYTES sort later without changing control.)
 *   • JUMP/JUMPI use relative skip counts and a total `dropK` function on K.
 *
 * Notes
 *   • This file is self-contained; it only uses Math sorts (Int, Bool), K, List, Map.
 *   • Gas costs are illustrative; tune to match spec/opcodes_vm_py.yaml.
 */

module ANIMICA-VM
  imports INT
  imports BOOL
  imports LIST
  imports MAP
  imports K-REFLECTION

  /***************************************************************************
  ** Configuration
  ***************************************************************************/

  configuration
    <animica>
      <k> $PGM:K </k>
      <gas> 0 </gas>                       // available gas
      <status> "RUNNING" </status>         // "RUNNING" | "SUCCESS" | "REVERT" | "OOG"
      <stack> .List </stack>               // List of Int (top is left-most)
      <mem> .Map </mem>                    // Map Int -> Int (ephemeral)
      <store> .Map </store>                // Map Int -> Int (persistent)
      <logs> .List </logs>                 // List of (topic,data) encoded as ListItem((topic,data))
      <ret> 0 </ret>                       // Int return value (bytes-as-int)
      <env>
        <blockTs> 0 </blockTs>
        <coinbase> 0 </coinbase>
        <caller> 0 </caller>
        <value> 0 </value>
        <chainId> 1 </chainId>
      </env>
      // Optional: deterministic seeds for syscalls (kept pure in this model)
      <seeds>
        <randSeed> 1 </randSeed>
      </seeds>
    </animica>

  /***************************************************************************
  ** Syntax
  ***************************************************************************/

  // Primitive instruction set (pure, deterministic)
  syntax Instr ::= "PUSH" Int
                 | "POP"
                 | "DUP" Int                   // 1-based
                 | "SWAP" Int                  // 1-based: swap top with Nth below
                 | "ADD" | "SUB" | "MUL" | "DIV" | "MOD"
                 | "LT" | "GT" | "EQ" | "NOT"
                 | "AND" | "OR" | "XOR"
                 | "MLOAD" Int | "MSTORE" Int  // ephemeral memory
                 | "SLOAD" Int | "SSTORE" Int  // persistent storage
                 | "SHA3"                      // pop x -> push sha3(x)
                 | "EMIT"                      // pop topic,data -> logs += (topic,data)
                 | "GAS"                       // push current gas
                 | "JUMP" Int                  // relative skip forward N instructions
                 | "JUMPI" Int                 // conditional relative skip (if top != 0)
                 | "RETURN"                    // pop v; success; halt
                 | "REVERT"                    // pop v (ignored here); revert; halt
                 | "HALT"                      // success; halt
                 | Syscall
                 [klabel(instr)]

  // Deterministic syscall surface used by capabilities/host (pure functions here)
  syntax Syscall ::= "BLOB_PIN"   // ns, size, digest -> commitment
                   | "AI_ENQ"     // modelId, promptHash -> taskId
                   | "Q_ENQ"      // circuitHash, shots, trapSeed -> taskId
                   | "RANDOM"     // n -> rand(n) derived from env.seed (pure)
                   [klabel(sys)]

  // Treat instructions as K items so the program is a K sequence
  syntax KItem ::= Instr

  /***************************************************************************
  ** Helpers: stack/memory utilities
  ***************************************************************************/

  // Shorthands for de/constructing stack (List of Int)
  syntax List ::= "⟂" [function]
  rule ⟂ => .List

  syntax Int ::= top(List) [function]
  rule top ( X:List ) => head X
       requires X =/=K .List
  // head from LIST module: ListItem(V) L => V

  // nth (1-based) from top: DUP/SWAP operand
  syntax Int ::= nth(List, Int) [function]
  rule nth ( ListItem(V:Int) _:List , 1 ) => V
  rule nth ( ListItem(_V) XS , N ) => nth(XS, N -Int 1) requires N >Int 1
  rule nth ( .List , _N ) => 0  // unreachable on well-typed programs; keeps totality

  // replace nth (1-based) below top with given value (used by SWAP)
  syntax List ::= replaceNth(List, Int, Int) [function]
  rule replaceNth ( ListItem(_X) XS , 1 , V ) => ListItem(V) XS
  rule replaceNth ( ListItem(Y) XS , N , V ) => ListItem(Y) replaceNth(XS, N -Int 1, V) requires N >Int 1
  rule replaceNth ( .List , _N , _V ) => .List

  // Drop N instructions from a K sequence (used by JUMP/JUMPI)
  syntax K ::= dropK(K, Int) [function]
  rule dropK ( KSEQ:K , 0 ) => KSEQ
  rule dropK ( (I:KItem ~> KREST:K) , N ) => dropK(KREST, N -Int 1) requires N >Int 0
  rule dropK ( .K , _N ) => .K

  /***************************************************************************
  ** Gas model (illustrative; tune from spec/opcodes_vm_py.yaml)
  ***************************************************************************/
  syntax Int ::= gasOf(Instr) [function]

  // Constants
  rule gasOf( PUSH _ )  => 2
  rule gasOf( POP     ) => 1
  rule gasOf( DUP  _  ) => 1
  rule gasOf( SWAP _  ) => 1

  rule gasOf( ADD ) => 3
  rule gasOf( SUB ) => 3
  rule gasOf( MUL ) => 5
  rule gasOf( DIV ) => 5
  rule gasOf( MOD ) => 5

  rule gasOf( LT )  => 3
  rule gasOf( GT )  => 3
  rule gasOf( EQ )  => 3
  rule gasOf( NOT ) => 2
  rule gasOf( AND ) => 3
  rule gasOf( OR  ) => 3
  rule gasOf( XOR ) => 3

  rule gasOf( MLOAD  _ ) => 5
  rule gasOf( MSTORE _ ) => 8
  rule gasOf( SLOAD  _ ) => 50
  rule gasOf( SSTORE _ ) => 200

  rule gasOf( SHA3 )   => 15
  rule gasOf( EMIT )   => 20
  rule gasOf( GAS )    => 1

  rule gasOf( JUMP  _ )  => 2
  rule gasOf( JUMPI _ )  => 3

  rule gasOf( RETURN ) => 0
  rule gasOf( REVERT ) => 0
  rule gasOf( HALT   ) => 0

  // Syscalls (pure here; priced)
  rule gasOf( BLOB_PIN ) => 100
  rule gasOf( AI_ENQ   ) => 120
  rule gasOf( Q_ENQ    ) => 150
  rule gasOf( RANDOM   ) => 10

  /***************************************************************************
  ** Uninterpreted (pure) helper functions
  ***************************************************************************/
  syntax Int ::= sha3Int(Int) [function]              // abstract but deterministic
  syntax Int ::= blobCommit(Int, Int, Int) [function] // ns, size, digest -> commitment
  syntax Int ::= aiTaskId(Int, Int, Int) [function]   // caller, modelId, promptHash
  syntax Int ::= qTaskId(Int, Int, Int) [function]    // caller, circuitHash, trapSeed
  syntax Int ::= randN(Int, Int) [function]           // seed, n -> Int (lower n bytes)

  // No defining equations: they are abstract; proofs can impose properties if needed.

  /***************************************************************************
  ** Safety guards (stack/mem existence)
  ***************************************************************************/

  // Pop one
  syntax List ::= pop1(List) [function]
  rule pop1( ListItem(_X) XS ) => XS
  rule pop1( .List ) => .List // total; runtime will REVERT before this by rule ordering

  // Pop two
  syntax List ::= pop2(List) [function]
  rule pop2( ListItem(_X) ListItem(_Y) XS ) => XS
  rule pop2( _ ) => .List

  // Safe map get with default 0 (keeps expressions total)
  syntax Int ::= mget(Map, Int) [function]
  rule mget(M, K) => M[K] requires K in_keys(M)
  rule mget(M, _K) => 0 requires notBool (_K in_keys(M))

  /***************************************************************************
  ** Execution rules
  ***************************************************************************/

  /* Halting & terminal states */
  rule <animica>
         <k> HALT ~> _ => .K </k>
         <status> _ => "SUCCESS" </status>
       ... </animica>

  rule <animica>
         <k> RETURN ~> _ => .K </k>
         <stack> ListItem(V:Int) ST => ST </stack>
         <ret> _ => V </ret>
         <status> _ => "SUCCESS" </status>
       ... </animica>

  rule <animica>
         <k> REVERT ~> _ => .K </k>
         <stack> ST </stack> // optional: pop one arg if present
         <status> _ => "REVERT" </status>
       ... </animica>

  /* Gas out: any step with insufficient gas flips to OOG and halts.
     We encode per-op OOG checks for clarity. */

  // Template macro (documentary): For each rule below, we always have a twin OOG rule like:
  //   if <gas> G < gasOf(OP) then -> status := "OOG"; <k> drops to .K

  /* Stack operations */
  rule <animica>
         <k> PUSH N:Int ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(PUSH N) </gas>
         <stack> ST => ListItem(N) ST </stack>
       ... </animica>
       requires gasOf(PUSH N) <=Int G

  rule <animica>
         <k> POP ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(POP) </gas>
         <stack> ListItem(_X) ST => ST </stack>
       ... </animica>
       requires gasOf(POP) <=Int G

  rule <animica>
         <k> DUP N:Int ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(DUP N) </gas>
         <stack> ST => ListItem(nth(ST, N)) ST </stack>
       ... </animica>
       requires N >=Int 1 andBool gasOf(DUP N) <=Int G

  rule <animica>
         <k> SWAP N:Int ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(SWAP N) </gas>
         <stack> ListItem(T) ST => ListItem(nth(ST, N)) replaceNth(ST, N, T) </stack>
       ... </animica>
       requires N >=Int 1 andBool gasOf(SWAP N) <=Int G

  /* Arithmetic / logic (unbounded Int; deterministic) */
  rule <animica>
         <k> ADD ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(ADD) </gas>
         <stack> ListItem(A) ListItem(B) ST => ListItem(A +Int B) ST </stack>
       ... </animica>
       requires gasOf(ADD) <=Int G
  rule <animica>
         <k> SUB ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(SUB) </gas>
         <stack> ListItem(A) ListItem(B) ST => ListItem(A -Int B) ST </stack>
       ... </animica>
       requires gasOf(SUB) <=Int G
  rule <animica>
         <k> MUL ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(MUL) </gas>
         <stack> ListItem(A) ListItem(B) ST => ListItem(A *Int B) ST </stack>
       ... </animica>
       requires gasOf(MUL) <=Int G
  rule <animica>
         <k> DIV ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(DIV) </gas>
         <stack> ListItem(A) ListItem(B) ST => ListItem( if B ==Int 0 then 0 else A /Int B fi ) ST </stack>
       ... </animica>
       requires gasOf(DIV) <=Int G
  rule <animica>
         <k> MOD ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(MOD) </gas>
         <stack> ListItem(A) ListItem(B) ST => ListItem( if B ==Int 0 then 0 else A modInt B fi ) ST </stack>
       ... </animica>
       requires gasOf(MOD) <=Int G

  rule <animica>
         <k> LT ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(LT) </gas>
         <stack> ListItem(A) ListItem(B) ST => ListItem( if A <Int B then 1 else 0 fi ) ST </stack>
       ... </animica>
       requires gasOf(LT) <=Int G
  rule <animica>
         <k> GT ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(GT) </gas>
         <stack> ListItem(A) ListItem(B) ST => ListItem( if A >Int B then 1 else 0 fi ) ST </stack>
       ... </animica>
       requires gasOf(GT) <=Int G
  rule <animica>
         <k> EQ ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(EQ) </gas>
         <stack> ListItem(A) ListItem(B) ST => ListItem( if A ==Int B then 1 else 0 fi ) ST </stack>
       ... </animica>
       requires gasOf(EQ) <=Int G
  rule <animica>
         <k> NOT ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(NOT) </gas>
         <stack> ListItem(A) ST => ListItem( if A ==Int 0 then 1 else 0 fi ) ST </stack>
       ... </animica>
       requires gasOf(NOT) <=Int G
  rule <animica>
         <k> AND ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(AND) </gas>
         <stack> ListItem(A) ListItem(B) ST => ListItem( (A =/=Int 0) andBool (B =/=Int 0) ? 1 : 0 ) ST </stack>
       ... </animica>
       requires gasOf(AND) <=Int G
  rule <animica>
         <k> OR ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(OR) </gas>
         <stack> ListItem(A) ListItem(B) ST => ListItem( (A =/=Int 0) orBool (B =/=Int 0) ? 1 : 0 ) ST </stack>
       ... </animica>
       requires gasOf(OR) <=Int G
  rule <animica>
         <k> XOR ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(XOR) </gas>
         <stack> ListItem(A) ListItem(B) ST
            => ListItem( ((A =/=Int 0) xorBool (B =/=Int 0)) ? 1 : 0 ) ST </stack>
       ... </animica>
       requires gasOf(XOR) <=Int G

  /* Memory & Storage */
  rule <animica>
         <k> MLOAD K:Int ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(MLOAD K) </gas>
         <mem> M => M </mem>
         <stack> ST => ListItem(mget(M,K)) ST </stack>
       ... </animica>
       requires gasOf(MLOAD K) <=Int G

  rule <animica>
         <k> MSTORE K:Int ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(MSTORE K) </gas>
         <stack> ListItem(V) ST => ST </stack>
         <mem> M => M[K <- V] </mem>
       ... </animica>
       requires gasOf(MSTORE K) <=Int G

  rule <animica>
         <k> SLOAD K:Int ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(SLOAD K) </gas>
         <store> S => S </store>
         <stack> ST => ListItem(mget(S,K)) ST </stack>
       ... </animica>
       requires gasOf(SLOAD K) <=Int G

  rule <animica>
         <k> SSTORE K:Int ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(SSTORE K) </gas>
         <stack> ListItem(V) ST => ST </stack>
         <store> S => S[K <- V] </store>
       ... </animica>
       requires gasOf(SSTORE K) <=Int G

  /* Hash & events */
  rule <animica>
         <k> SHA3 ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(SHA3) </gas>
         <stack> ListItem(A) ST => ListItem(sha3Int(A)) ST </stack>
       ... </animica>
       requires gasOf(SHA3) <=Int G

  // EMIT: pop (topic, data) and append to logs as a pair (modeled as ListItem(topic) ListItem(data))
  syntax KItem ::= "(" Int "," Int ")" [klabel(pair)]
  rule <animica>
         <k> EMIT ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(EMIT) </gas>
         <stack> ListItem(TOPIC) ListItem(DATA) ST => ST </stack>
         <logs> LOGS => LOGS ListItem( (TOPIC, DATA) ) </logs>
       ... </animica>
       requires gasOf(EMIT) <=Int G

  /* GAS (introspection) */
  rule <animica>
         <k> GAS ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(GAS) </gas>
         <stack> ST => ListItem(G) ST </stack>
       ... </animica>
       requires gasOf(GAS) <=Int G

  /* Control flow with relative skip */
  rule <animica>
         <k> JUMP N ~> KREST => dropK(KREST, N) </k>
         <gas> G => G -Int gasOf(JUMP N) </gas>
       ... </animica>
       requires N >=Int 0 andBool gasOf(JUMP N) <=Int G

  rule <animica>
         <k> JUMPI N ~> KREST => dropK(KREST, N) </k>
         <gas> G => G -Int gasOf(JUMPI N) </gas>
         <stack> ListItem(COND) ST => ST </stack>
       ... </animica>
       requires N >=Int 0 andBool COND =/=Int 0 andBool gasOf(JUMPI N) <=Int G

  // JUMPI with zero condition: fall through
  rule <animica>
         <k> JUMPI N ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(JUMPI N) </gas>
         <stack> ListItem(COND) ST => ST </stack>
       ... </animica>
       requires N >=Int 0 andBool COND ==Int 0 andBool gasOf(JUMPI N) <=Int G

  /* Syscalls (pure, deterministic here) */
  // BLOB_PIN(ns,size,digest) -> commitment
  rule <animica>
         <k> BLOB_PIN ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(BLOB_PIN) </gas>
         <stack> ListItem(NS) ListItem(SZ) ListItem(DIG) ST
              => ListItem(blobCommit(NS,SZ,DIG)) ST </stack>
       ... </animica>
       requires gasOf(BLOB_PIN) <=Int G

  // AI_ENQ(modelId,promptHash) -> taskId (bound to env.caller)
  rule <animica>
         <k> AI_ENQ ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(AI_ENQ) </gas>
         <stack> ListItem(MODEL) ListItem(PROMPT) ST
              => ListItem(aiTaskId(CALLER, MODEL, PROMPT)) ST </stack>
         <env> <caller> CALLER </caller> ... </env>
       ... </animica>
       requires gasOf(AI_ENQ) <=Int G

  // Q_ENQ(circuitHash, shots, trapSeed) -> taskId (bound to env.caller)
  rule <animica>
         <k> Q_ENQ ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(Q_ENQ) </gas>
         <stack> ListItem(CIRCUIT) ListItem(SHOTS) ListItem(TRAP) ST
              => ListItem(qTaskId(CALLER, CIRCUIT, TRAP)) ST </stack>
         <env> <caller> CALLER </caller> ... </env>
       ... </animica>
       requires SHOTS >=Int 0 andBool gasOf(Q_ENQ) <=Int G

  // RANDOM(n) -> randN(seed, n)
  rule <animica>
         <k> RANDOM ~> KREST => KREST </k>
         <gas> G => G -Int gasOf(RANDOM) </gas>
         <stack> ListItem(NBYTES) ST => ListItem(randN(SEED, NBYTES)) ST </stack>
         <seeds> <randSeed> SEED </randSeed> </seeds>
       ... </animica>
       requires NBYTES >=Int 0 andBool gasOf(RANDOM) <=Int G

  /***************************************************************************
  ** Out-of-gas semantics (per-op)
  ***************************************************************************/

  rule <animica>
         <k> (PUSH N) ~> _ => .K </k>
         <gas> G </gas>
         <status> _ => "OOG" </status>
       ... </animica>
       requires G <Int gasOf(PUSH N)

  rule <animica> <k> (I:Instr) ~> _ => .K </k> <gas> G </gas> <status> _ => "OOG" </status> ... </animica>
       requires I =/=K PUSH 0  // prevent overlap with previous rule head
        andBool G <Int gasOf(I)

  /***************************************************************************
  ** Sanity: running to terminal states
  ***************************************************************************/

  // If program ends (no instructions) while RUNNING, we HALT successfully.
  rule <animica>
         <k> .K </k>
         <status> "RUNNING" => "SUCCESS" </status>
       ... </animica>

endmodule
