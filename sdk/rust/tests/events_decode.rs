// Event decoding tests
// --------------------
// These tests validate that the Rust SDK can construct an event decoder
// from a real ABI (Counter example) and that it handles unknown /
// malformed logs gracefully without panicking.
//
// They do NOT require a running node.

use serde_json::Value as JsonValue;

#[test]
fn decoder_constructs_from_counter_abi() -> Result<(), Box<dyn std::error::Error>> {
    let abi_json = include_str!("../../common/examples/counter_abi.json");

    // Prefer the strongly-typed decoder API if available.
    // The events module should expose an EventDecoder that can be built from ABI JSON.
    {
        use animica_sdk::contracts::events::EventDecoder;
        let _dec = EventDecoder::from_abi_json(abi_json)?;
    }

    // Also ensure the ABI itself parses as JSON for sanity.
    let v: JsonValue = serde_json::from_str(abi_json)?;
    assert!(v.get("functions").is_some(), "ABI should have functions[]");
    Ok(())
}

#[test]
fn unknown_topic_is_ignored_or_reported_cleanly() -> Result<(), Box<dyn std::error::Error>> {
    use animica_sdk::contracts::events::{EventDecoder, RawLog};

    let abi_json = include_str!("../../common/examples/counter_abi.json");
    let dec = EventDecoder::from_abi_json(abi_json)?;

    // A synthetic log with a made-up topic that won't match any event.
    let raw = RawLog {
        address: "anim1testunknownxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx".to_string(),
        topics: vec!["0xdeadbeef".to_string()],
        data: "0x".to_string(),
        index: Some(0),
    };

    // Implementations may either return Ok(None) (not decoded) or an Err describing
    // the reason; both behaviors are acceptable as long as they are clean and non-panicking.
    match dec.decode_log(&raw) {
        Ok(None) => {
            // not decoded — acceptable
        }
        Ok(Some(ev)) => {
            panic!("unexpectedly decoded log as event: {:?}", ev);
        }
        Err(e) => {
            // Clean error is acceptable; just print it for visibility.
            eprintln!("decoder returned error (acceptable for unknown topic): {e}");
        }
    }

    Ok(())
}

#[test]
fn batch_decode_handles_mixed_inputs() -> Result<(), Box<dyn std::error::Error>> {
    use animica_sdk::contracts::events::{EventDecoder, RawLog};

    let abi_json = include_str!("../../common/examples/counter_abi.json");
    let dec = EventDecoder::from_abi_json(abi_json)?;

    // Mix: empty log, unknown topic, and empty topics with non-hex data.
    let logs = vec![
        RawLog {
            address: "anim1empty000000000000000000000000000000000".into(),
            topics: vec![],
            data: "0x".into(),
            index: Some(0),
        },
        RawLog {
            address: "anim1unknown000000000000000000000000000000".into(),
            topics: vec!["0xdeadbeef".into()],
            data: "0x".into(),
            index: Some(1),
        },
        RawLog {
            address: "anim1badhex000000000000000000000000000000".into(),
            topics: vec![],
            data: "0xzz".into(), // invalid hex
            index: Some(2),
        },
    ];

    // Batch decode should never panic. It may:
    //  - drop undecodable entries and return a subset,
    //  - or return an error describing the first failure.
    match dec.decode_logs(&logs) {
        Ok(decoded) => {
            // With synthetic logs above, most decoders will yield 0 decoded events.
            assert!(
                decoded.is_empty(),
                "expected no decodable events from synthetic logs, got {}",
                decoded.len()
            );
        }
        Err(e) => {
            // Clean error path is acceptable; ensure it's a sensible message.
            let msg = format!("{e}");
            assert!(
                msg.to_lowercase().contains("hex")
                    || msg.to_lowercase().contains("topic")
                    || msg.to_lowercase().contains("decode"),
                "unexpected error message: {msg}"
            );
        }
    }

    Ok(())
}
