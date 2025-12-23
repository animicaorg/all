# Studio: Deterministic Proof Scripts

This guide explains how to author deterministic proof scripts in Studio, export
portable artifacts, and generate test vectors that can be verified by nodes and
miners.

## Authoring a proof script

1. Open **Studio → Templates** and choose **Mining Proof Script**.
2. Edit the required entrypoints:
   - `derive_challenge(inputs)`
   - `verify_proof(inputs)`
   - `commit_outputs(outputs)`
3. Keep logic deterministic: no wall clock, network, filesystem, or nondeterministic
   APIs. Only the deterministic stdlib subset is allowed.

## Exporting a ScriptArtifact

1. Compile the script in Studio.
2. Open the **Artifacts** panel.
3. Under **Deterministic Script Artifact**, click **Export Artifact**.
4. The exported JSON contains:
   - `manifest`
   - `sources` (base64-encoded source files)
   - `compiled_b64` (deterministic bytecode)
   - `artifact_hash` (sha3-256 over canonical CBOR bundle)

The exported artifact can be installed directly by the node/miner CLI.

## Generating test vectors

1. In the **Artifacts** panel, select an entrypoint.
2. Provide inputs as JSON (Studio converts to canonical CBOR).
3. Click **Generate Test Vector**.
4. Download the generated `script_vector.json` and verify it with:

```
animica script test-vector-verify script_vector.json
```

## Notes

- The Studio compiler uses the same deterministic VM bytecode as node/miner.
- Script artifacts are portable: the exported bundle can be installed without
  modification.
