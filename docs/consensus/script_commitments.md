# Consensus: Script Commitments

Deterministic proof scripts are identified by their `artifact_hash`, a sha3-256
hash over the canonical ScriptArtifact bundle (manifest, sources, compiled
bytecode, and VM/ABI versions encoded as canonical CBOR).

Blocks and jobs may carry the following commitments:

- `script_hash` — the ScriptArtifact hash
- `inputs_commit` — sha3-256 of canonical CBOR-encoded inputs
- `outputs_commit` — sha3-256 of canonical CBOR-encoded outputs

These commitments allow consensus verification to replay deterministic scripts
and reject blocks that do not match the declared inputs/outputs.

## Allowed scripts

Nodes should only accept proofs from pinned/approved script hashes in
production. Use the CLI to pin scripts locally:

```bash
animica script pin <script_hash>
```

Development and test networks may opt in to unpinned scripts via configuration.
