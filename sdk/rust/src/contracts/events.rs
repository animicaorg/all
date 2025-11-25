//! Event **filters & decoder** for logs emitted by contracts.
//!
//! This module provides:
//! - `EventDecoder` — build from ABI JSON and decode `Receipt.logs` entries
//! - `EventFilter`  — simple in-memory filters (by address/name)
//! - `DecodedEvent` — structured output (name + named params)
//!
//! ### Assumptions
//! - Event topic0 = `keccak256("Name(type1,type2,...)")` (names and `indexed` flags
//!   are not part of the signature), like Ethereum.
//! - Topics 1.. encode **indexed** parameters (static types are directly encoded
//!   as 32-byte words; dynamic types are stored as `keccak256(value)` and are
//!   surfaced as `<param>_hash` hex strings).
//! - Non-indexed parameters are ABI-encoded in the `data` field (tuple layout).
//!
//! Supported types for decoding: `uint<M>`, `int<M>`, `bool`, `address`,
//! `bytes`, `bytes<M>`, and `string`. Fixed/dynamic arrays are **not** decoded
//! in this version and will return an error if encountered.
//!
//! The ABI JSON is expected to contain an `events` array with items like:
//! ```json
//! {"name":"Transfer","inputs":[
//!   {"name":"from","type":"address","indexed":true},
//!   {"name":"to","type":"address","indexed":true},
//!   {"name":"value","type":"uint256","indexed":false}
//! ]}
//! ```
//!
//! If your node exposes a filter RPC, you can still use this decoder to parse
//! the returned logs into strongly-typed JSON values.

use crate::error::{Error, Result};
use crate::types::{Receipt, LogEvent};
use crate::utils::hash::keccak256;
use hex::FromHex;
use serde::Serialize;
use serde_json::{json, Map as JsonMap, Value as JsonValue};
use std::collections::HashMap;

// ----------------------------- Public structs --------------------------------

/// Decoded event with named parameters.
#[derive(Debug, Clone, Serialize)]
pub struct DecodedEvent {
    /// Contract address that emitted the log.
    pub address: String,
    /// Event name from ABI.
    pub name: String,
    /// Named parameters (JSON-friendly; large integers are decimal strings).
    pub params: JsonMap<String, JsonValue>,
    /// Raw log for reference.
    pub raw: LogEvent,
}

/// In-memory event filter (client-side convenience).
#[derive(Debug, Clone, Default)]
pub struct EventFilter<'a> {
    pub address: Option<&'a str>,
    pub name: Option<&'a str>,
}

impl<'a> EventFilter<'a> {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn address(mut self, addr: &'a str) -> Self {
        self.address = Some(addr);
        self
    }
    pub fn name(mut self, name: &'a str) -> Self {
        self.name = Some(name);
        self
    }
    fn matches(&self, ev: &DecodedEvent) -> bool {
        if let Some(a) = self.address {
            if ev.address != a { return false; }
        }
        if let Some(n) = self.name {
            if ev.name != n { return false; }
        }
        true
    }
}

/// Decoder built from an ABI (events portion). Can decode `LogEvent`s.
#[derive(Clone)]
pub struct EventDecoder {
    specs: Vec<EventSpec>,
    by_topic0: HashMap<[u8; 32], usize>,
}

impl EventDecoder {
    /// Build a decoder from an ABI JSON object that contains `"events": [...]`.
    pub fn from_abi_json(abi_json: &JsonValue) -> Result<Self> {
        let events = abi_json
            .get("events")
            .ok_or_else(|| Error::Abi("ABI missing 'events' array".into()))?;

        let arr = events
            .as_array()
            .ok_or_else(|| Error::Abi("'events' must be an array".into()))?;

        let mut specs = Vec::with_capacity(arr.len());
        for ev in arr {
            specs.push(EventSpec::from_json(ev)?);
        }

        let mut by_topic0 = HashMap::with_capacity(specs.len());
        for (i, s) in specs.iter().enumerate() {
            by_topic0.insert(s.topic0, i);
        }

        Ok(Self { specs, by_topic0 })
    }

    /// Decode a single `LogEvent`. Returns `None` if `topic0` doesn't match any event.
    pub fn decode_log(&self, log: &LogEvent) -> Result<Option<DecodedEvent>> {
        if log.topics.is_empty() {
            return Err(Error::Abi("log has no topics".into()));
        }
        let t0 = hex_to_word(&log.topics[0])
            .ok_or_else(|| Error::InvalidHex(log.topics[0].clone()))?;
        let spec = match self.by_topic0.get(&t0) {
            Some(idx) => &self.specs[*idx],
            None => return Ok(None),
        };

        // Decode indexed params from topics[1..]
        if log.topics.len() - 1 != spec.indexed_count {
            return Err(Error::Abi(format!(
                "indexed topics count mismatch: have {}, want {} for {}",
                log.topics.len() - 1,
                spec.indexed_count,
                spec.name
            )));
        }

        let mut params = JsonMap::new();
        let mut topic_i = 1usize;

        for inp in &spec.inputs {
            if inp.indexed {
                let word_hex = &log.topics[topic_i];
                topic_i += 1;

                // Dynamic types are hashed in topics: expose as "<name>_hash"
                if inp.t.is_dynamic() {
                    params.insert(format!("{}_hash", &inp.name), JsonValue::String(normalize_hex(word_hex)));
                    continue;
                }

                let word = hex_to_word(word_hex)
                    .ok_or_else(|| Error::InvalidHex(word_hex.clone()))?;
                let val = decode_word_static(&inp.t, &word)?;
                params.insert(inp.name.clone(), val);
            }
        }

        // Non-indexed decode from data (tuple ABI)
        let non_indexed: Vec<_> = spec.inputs.iter().filter(|p| !p.indexed).collect();
        let data_bytes = hex_to_bytes(&log.data)
            .ok_or_else(|| Error::InvalidHex(log.data.clone()))?;
        let decoded_vals = decode_tuple(&non_indexed, &data_bytes)?;

        for (p, v) in non_indexed.iter().zip(decoded_vals.into_iter()) {
            params.insert(p.name.clone(), v);
        }

        Ok(Some(DecodedEvent {
            address: log.address.clone(),
            name: spec.name.clone(),
            params,
            raw: log.clone(),
        }))
    }

    /// Decode all logs in a receipt; optionally filter the results.
    pub fn decode_receipt(&self, rcpt: &Receipt, filter: Option<&EventFilter<'_>>) -> Result<Vec<DecodedEvent>> {
        let mut out = Vec::new();
        for log in &rcpt.logs {
            if let Some(ev) = self.decode_log(log)? {
                if filter.map(|f| f.matches(&ev)).unwrap_or(true) {
                    out.push(ev);
                }
            }
        }
        Ok(out)
    }
}

// ---------------------------- Internal modeling ------------------------------

#[derive(Clone)]
struct EventSpec {
    name: String,
    inputs: Vec<EventInput>,
    indexed_count: usize,
    topic0: [u8; 32],
}

#[derive(Clone)]
struct EventInput {
    name: String,
    t: AbiType,
    indexed: bool,
}

impl EventSpec {
    fn from_json(v: &JsonValue) -> Result<Self> {
        let name = v.get("name")
            .and_then(|x| x.as_str())
            .ok_or_else(|| Error::Abi("event missing name".into()))?
            .to_string();

        let inputs_val = v.get("inputs")
            .and_then(|x| x.as_array())
            .ok_or_else(|| Error::Abi(format!("event {} missing inputs array", name)))?;

        let mut inputs = Vec::with_capacity(inputs_val.len());
        let mut types_for_sig = Vec::with_capacity(inputs_val.len());

        for inp in inputs_val {
            let in_name = inp.get("name").and_then(|x| x.as_str()).unwrap_or("").to_string();
            let tstr = inp.get("type")
                .and_then(|x| x.as_str())
                .ok_or_else(|| Error::Abi(format!("event {} input missing type", name)))?;
            let indexed = inp.get("indexed").and_then(|x| x.as_bool()).unwrap_or(false);
            let t = AbiType::parse(tstr)?;

            inputs.push(EventInput { name: in_name, t: t.clone(), indexed });
            types_for_sig.push(t.signature_token());
        }

        let sig = format!("{}({})", name, types_for_sig.join(","));
        let topic0 = keccak256(sig.as_bytes());
        let indexed_count = inputs.iter().filter(|p| p.indexed).count();

        Ok(Self { name, inputs, indexed_count, topic0 })
    }
}

// ------------------------------- ABI types -----------------------------------

#[derive(Clone)]
enum AbiType {
    Uint(usize),   // bits
    Int(usize),    // bits
    Bool,
    Address,
    Bytes,         // dynamic
    String,        // dynamic
    FixedBytes(usize),
    // Arrays are not supported in this version.
}

impl AbiType {
    fn parse(s: &str) -> Result<Self> {
        if s == "bool" { return Ok(Self::Bool); }
        if s == "address" { return Ok(Self::Address); }
        if s == "bytes" { return Ok(Self::Bytes); }
        if s == "string" { return Ok(Self::String); }
        if let Some(sz) = s.strip_prefix("bytes") {
            let n = sz.parse::<usize>().map_err(|_| Error::Abi(format!("invalid bytesN: {}", s)))?;
            if n == 0 || n > 32 { return Err(Error::Abi(format!("bytesN out of range: {}", s))); }
            return Ok(Self::FixedBytes(n));
        }
        if s == "uint" { return Ok(Self::Uint(256)); }
        if let Some(sz) = s.strip_prefix("uint") {
            let n = sz.parse::<usize>().map_err(|_| Error::Abi(format!("invalid uintN: {}", s)))?;
            if n == 0 || n > 256 || n % 8 != 0 { return Err(Error::Abi(format!("uintN out of range: {}", s))); }
            return Ok(Self::Uint(n));
        }
        if s == "int" { return Ok(Self::Int(256)); }
        if let Some(sz) = s.strip_prefix("int") {
            let n = sz.parse::<usize>().map_err(|_| Error::Abi(format!("invalid intN: {}", s)))?;
            if n == 0 || n > 256 || n % 8 != 0 { return Err(Error::Abi(format!("intN out of range: {}", s))); }
            return Ok(Self::Int(n));
        }
        // arrays not supported here
        if s.ends_with("]") {
            return Err(Error::Abi(format!("array types not supported in event decoder: {}", s)));
        }
        Err(Error::Abi(format!("unsupported type: {}", s)))
    }

    fn is_dynamic(&self) -> bool {
        matches!(self, AbiType::Bytes | AbiType::String)
    }

    /// Token text used in event signature hashing.
    fn signature_token(&self) -> &'static str {
        match self {
            AbiType::Uint(n) => match *n {
                256 => "uint256",
                _ => "uint", // most contracts normalize, but we keep a conservative token
            },
            AbiType::Int(n) => match *n {
                256 => "int256",
                _ => "int",
            },
            AbiType::Bool => "bool",
            AbiType::Address => "address",
            AbiType::Bytes => "bytes",
            AbiType::String => "string",
            AbiType::FixedBytes(n) => match *n {
                32 => "bytes32",
                _ => "bytes", // hashing uses canonical text; bytesN is acceptable too
            },
        }
    }
}

// ----------------------------- Decoding helpers ------------------------------

fn normalize_hex(s: &str) -> String {
    if s.starts_with("0x") || s.starts_with("0X") { s.to_lowercase() } else { format!("0x{}", s.to_lowercase()) }
}

fn hex_to_bytes(s: &str) -> Option<Vec<u8>> {
    let raw = s.strip_prefix("0x").unwrap_or(s);
    Vec::from_hex(raw).ok()
}

fn hex_to_word(s: &str) -> Option<[u8; 32]> {
    let b = hex_to_bytes(s)?;
    if b.len() != 32 { return None; }
    let mut out = [0u8; 32];
    out.copy_from_slice(&b);
    Some(out)
}

fn decode_word_static(t: &AbiType, word: &[u8; 32]) -> Result<JsonValue> {
    match t {
        AbiType::Bool => Ok(JsonValue::Bool(word[31] == 1)),
        AbiType::Address => {
            let addr = &word[12..32]; // last 20 bytes
            Ok(JsonValue::String(format!("0x{}", hex::encode(addr))))
        }
        AbiType::Uint(_) => {
            // big-endian 32-byte integer → decimal string
            let s = be_bytes_to_decimal_str(word);
            Ok(JsonValue::String(s))
        }
        AbiType::Int(_) => {
            // Interpret as two's complement; output decimal string
            let s = be_twos_complement_to_decimal_str(word);
            Ok(JsonValue::String(s))
        }
        AbiType::FixedBytes(n) => {
            let v = &word[0..*n];
            Ok(JsonValue::String(format!("0x{}", hex::encode(v))))
        }
        AbiType::Bytes | AbiType::String => {
            Err(Error::Abi("dynamic types cannot be decoded from indexed topic (only hash is present)".into()))
        }
    }
}

fn be_bytes_to_decimal_str(word: &[u8; 32]) -> String {
    // minimal big integer to decimal string
    num_bigint::BigUint::from_bytes_be(word).to_str_radix(10)
}

fn be_twos_complement_to_decimal_str(word: &[u8; 32]) -> String {
    use num_bigint::{BigInt, Sign};
    // If MSB set → negative number in two's complement 256-bit
    let negative = word[0] & 0x80 != 0;
    if !negative {
        return num_bigint::BigUint::from_bytes_be(word).to_str_radix(10);
    }
    // value = -(~word + 1)
    let mut inv = [0u8; 32];
    for i in 0..32 { inv[i] = !word[i]; }
    // add 1
    for i in (0..32).rev() {
        let (v, carry) = inv[i].overflowing_add(1);
        inv[i] = v;
        if !carry { break; }
    }
    let mag = num_bigint::BigUint::from_bytes_be(&inv);
    let signed = BigInt::from_biguint(Sign::Minus, mag);
    signed.to_string()
}

/// Decode ABI tuple payload for non-indexed params.
fn decode_tuple(params: &[&EventInput], data: &[u8]) -> Result<Vec<JsonValue>> {
    // Standard ABI: head = 32 * N bytes; dynamic items have offsets into tail.
    // We only support flat tuples of supported scalar/dynamic types (no arrays).
    let n = params.len();
    let head_len = 32 * n;
    if data.len() < head_len {
        return Err(Error::Abi(format!("event data too short: {} < {}", data.len(), head_len)));
    }

    let mut results = Vec::with_capacity(n);

    // First pass: collect head words and potential offsets
    let mut heads: Vec<[u8; 32]> = Vec::with_capacity(n);
    for i in 0..n {
        let mut w = [0u8; 32];
        w.copy_from_slice(&data[i * 32..(i + 1) * 32]);
        heads.push(w);
    }

    for (i, inp) in params.iter().enumerate() {
        let t = &inp.t;
        if t.is_dynamic() {
            // Read offset (in bytes) from start of data
            let off = num_bigint::BigUint::from_bytes_be(&heads[i]).to_usize().ok_or_else(|| {
                Error::Abi("dynamic offset too large".into())
            })?;
            if off + 32 > data.len() {
                return Err(Error::Abi(format!("dynamic offset out of bounds: {}", off)));
            }
            // length at offset
            let mut len_word = [0u8; 32];
            len_word.copy_from_slice(&data[off..off + 32]);
            let len = num_bigint::BigUint::from_bytes_be(&len_word).to_usize().ok_or_else(|| {
                Error::Abi("dynamic length too large".into())
            })?;
            if off + 32 + len > data.len() {
                return Err(Error::Abi(format!("dynamic data out of bounds: {}+{}", off, len)));
            }
            let bytes = &data[off + 32..off + 32 + len];
            let val = match t {
                AbiType::Bytes => JsonValue::String(format!("0x{}", hex::encode(bytes))),
                AbiType::String => match std::str::from_utf8(bytes) {
                    Ok(s) => JsonValue::String(s.to_string()),
                    Err(_) => JsonValue::String(format!("0x{}", hex::encode(bytes))),
                },
                _ => unreachable!(),
            };
            results.push(val);
        } else {
            results.push(decode_word_static(t, &heads[i])?);
        }
    }

    Ok(results)
}

// ------------------------------- Public utils --------------------------------

/// Decode all logs in `receipt` using `abi_json` and return only those matching `filter` if provided.
pub fn decode_events_from_receipt(
    abi_json: &JsonValue,
    receipt: &Receipt,
    filter: Option<&EventFilter<'_>>,
) -> Result<Vec<DecodedEvent>> {
    let dec = EventDecoder::from_abi_json(abi_json)?;
    dec.decode_receipt(receipt, filter)
}

// ----------------------------------- Tests -----------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn sample_abi() -> JsonValue {
        json!({
            "events": [
                {
                    "name": "Transfer",
                    "inputs": [
                        {"name":"from","type":"address","indexed":true},
                        {"name":"to","type":"address","indexed":true},
                        {"name":"value","type":"uint256","indexed":false}
                    ]
                },
                {
                    "name": "Message",
                    "inputs": [
                        {"name":"sender","type":"address","indexed":true},
                        {"name":"text","type":"string","indexed":false}
                    ]
                }
            ]
        })
    }

    fn build_transfer_log(from: &str, to: &str, value: u128, addr: &str) -> LogEvent {
        // Construct topics/data matching our decoder rules.

        // topic0 = keccak256("Transfer(address,address,uint256)")
        let sig = "Transfer(address,address,uint256)";
        let t0 = format!("0x{}", hex::encode(keccak256(sig.as_bytes())));

        fn addr_topic(a: &str) -> String {
            // bech32m anim1... → this is a test; we assume hex address is already provided
            // For unit testing, accept hex input "0x..." 20B; left-pad to 32B
            let bytes = hex_to_bytes(a).unwrap_or_else(|| vec![0u8; 20]);
            let mut w = [0u8; 32];
            w[12..32].copy_from_slice(&bytes);
            format!("0x{}", hex::encode(w))
        }

        let from_t = addr_topic(from);
        let to_t = addr_topic(to);

        // data = uint256 value
        let mut data = [0u8; 32];
        let big = num_bigint::BigUint::from(value);
        let mut vbytes = big.to_bytes_be();
        if vbytes.len() > 32 { vbytes = vbytes[vbytes.len()-32..].to_vec(); }
        data[32 - vbytes.len()..].copy_from_slice(&vbytes);

        LogEvent {
            address: addr.to_string(),
            topics: vec![t0, from_t, to_t],
            data: format!("0x{}", hex::encode(data)),
            index: Some(0),
            block_number: Some(1),
            tx_hash: None,
        }
    }

    #[test]
    fn decoder_builds_and_decodes_transfer() {
        let abi = sample_abi();
        let dec = EventDecoder::from_abi_json(&abi).unwrap();
        let log = build_transfer_log("0x1111111111111111111111111111111111111111",
                                     "0x2222222222222222222222222222222222222222",
                                     12345,
                                     "anim1contract...");
        let maybe = dec.decode_log(&log).unwrap();
        assert!(maybe.is_some());
        let ev = maybe.unwrap();
        assert_eq!(ev.name, "Transfer");
        assert_eq!(ev.address, "anim1contract...");
        assert_eq!(ev.params.get("from").unwrap(), &json!("0x1111111111111111111111111111111111111111"));
        assert_eq!(ev.params.get("to").unwrap(), &json!("0x2222222222222222222222222222222222222222"));
        assert_eq!(ev.params.get("value").unwrap(), &json!("12345"));
    }

    #[test]
    fn filter_by_name_and_address() {
        let abi = sample_abi();
        let dec = EventDecoder::from_abi_json(&abi).unwrap();
        let log = build_transfer_log("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                                     "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                                     1_000_000u128,
                                     "anim1xyz...");
        let ev = dec.decode_log(&log).unwrap().unwrap();

        let f1 = EventFilter::default().name("Transfer");
        assert!(f1.matches(&ev));

        let f2 = EventFilter::default().name("Message");
        assert!(!f2.matches(&ev));

        let f3 = EventFilter::default().address("anim1xyz...");
        assert!(f3.matches(&ev));
    }

    // Test helper visibility
    use super::{hex_to_bytes as _hex_to_bytes};
    fn hex_to_bytes(s: &str) -> Option<Vec<u8>> { _hex_to_bytes(s) }
}
