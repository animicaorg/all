# Mining: Deterministic Proof Scripts

Deterministic proof scripts are portable artifacts used by miners and pools to
derive challenges, verify proofs, and commit outputs. They are identified by
an `artifact_hash` computed over the canonical ScriptArtifact bundle.

## Install and verify artifacts

```bash
# Verify artifact hash
animica script verify ./script_artifact.json

# Install locally (stored under ~/.animica/scripts/<hash>/)
animica script install ./script_artifact.json --pin
```

Pinned scripts are allowed for consensus verification on your node.

## Stratum jobs

Stratum jobs may include script metadata:

- `scriptHash`
- `inputsCommit`
- `outputsCommit`

These values are committed in the job id so miners can validate the exact
script inputs and outputs they are expected to execute.

## Test vectors

Studio can generate deterministic test vectors for scripts. Verify them with:

```bash
animica script test-vector-verify ./script_vector.json
```

This checks that the output commitment matches the canonical CBOR encoding.
