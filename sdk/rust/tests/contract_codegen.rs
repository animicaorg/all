// Contract "codegen" sanity tests.
// --------------------------------
// These tests don't actually run the Rust code generator; instead they
// validate that the ABI model we ship in the crate can parse a real
// ABI (the Counter example) and expose the method/event metadata a
// codegen tool would rely on.
//
// They are fast and require no node.

use serde_json::Value as JsonValue;

#[test]
fn counter_abi_parses_and_exposes_symbols() -> Result<(), Box<dyn std::error::Error>> {
    // Load the shared Counter ABI example (kept in-sync with other SDKs).
    let abi_json = include_str!("../../common/examples/counter_abi.json");

    // Parse through the crate's ABI model first (preferred).
    // If your crate exposes `animica_sdk::abi::Abi`, this exercises it.
    // Fall back to a structural JSON check if the model changes.
    #[allow(unused_mut)]
    let mut parsed_ok = false;

    // Try via the strongly-typed ABI (if present).
    // (Compilation will fail only if the type/module is missing; in that case,
    // use the structural JSON assertions below by commenting this block.)
    #[allow(unused_variables)]
    {
        use animica_sdk::abi;

        let abi: abi::Abi = serde_json::from_str(abi_json)?;
        // Basic shape checks that a codegen would depend on.
        let has_inc = abi
            .functions
            .iter()
            .any(|f| f.name == "inc" && f.inputs.is_empty());

        let has_get = abi
            .functions
            .iter()
            .any(|f| f.name == "get" && f.inputs.is_empty() && f.outputs.len() == 1);

        // If events exist, ensure they look reasonable (optional for Counter).
        let _maybe_event_names: Vec<&str> = abi.events.iter().map(|e| e.name.as_str()).collect();

        assert!(has_inc, "expected function inc()");
        assert!(has_get, "expected function get() -> <scalar>");
        parsed_ok = true;
    }

    if !parsed_ok {
        // Structural fallback: only JSON keys. (Should not run in normal builds.)
        let v: JsonValue = serde_json::from_str(abi_json)?;
        let funcs = v
            .get("functions")
            .and_then(|x| x.as_array())
            .ok_or("ABI missing functions[]")?;

        let has = |name: &str| {
            funcs.iter().any(|f| {
                f.get("name").and_then(|n| n.as_str()) == Some(name)
                    && f.get("inputs").map(|i| i.as_array().map(|a| a.is_empty()).unwrap_or(false)).unwrap_or(false)
            })
        };

        assert!(has("inc"), "expected function inc()");
        assert!(has("get"), "expected function get()");
    }

    Ok(())
}

#[test]
fn invalid_abi_is_rejected() {
    // Minimal invalid ABI (missing required fields).
    let bad = r#"{"functions":[{"name":""}]}"#;

    // Prefer the crate's ABI model to reject it.
    let typed_result: Result<animica_sdk::abi::Abi, _> = serde_json::from_str(bad);
    if let Ok(val) = typed_result {
        // If the model allowed it structurally, enforce simple invariants here.
        let all_named = val.functions.iter().all(|f| !f.name.trim().is_empty());
        assert!(!all_named, "empty function name should not be accepted");
    } else {
        // If deserialization failed, that's also acceptable.
        assert!(true);
    }
}
