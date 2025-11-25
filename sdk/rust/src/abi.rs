//! Minimal ABI model + validation for Animica contracts.
//!
//! The ABI mirrors the JSON structure used by the SDKs in Python/TS:
//!
//! ```json
//! {
//!   "name": "Counter",
//!   "functions": [
//!     {"name":"inc","inputs":[{"name":"delta","type":"u64"}],"outputs":[]},
//!     {"name":"get","inputs":[],"outputs":[{"name":"value","type":"u64"}]}
//!   ],
//!   "events": [
//!     {"name":"Incremented","inputs":[{"name":"by","type":"u64","indexed":true}]}
//!   ],
//!   "errors": [
//!     {"name":"RevertReason","inputs":[{"name":"msg","type":"string"}]}
//!   ]
//! }
//! ```
//!
//! Validation checks:
//! - Contract/function/event/error names must be non-empty and match `[A-Za-z_][A-Za-z0-9_]*`.
//! - No duplicate function/event/error names within the ABI.
//! - Parameter names must be unique within a function/event and valid identifiers (empty allowed for returns).
//! - Parameter types must be recognized (`bool`, signed/unsigned ints, `bytes`, `bytes<N>`, `string`,
//!   `address`, and `<type>[]` arrays).
//! - Event `indexed` parameters must not be dynamic (`bytes`, `string`, arrays).
//!
//! This module is intentionally conservative; encoding/decoding is handled elsewhere.

use crate::error::{Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

/// Top-level ABI document.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Abi {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(default)]
    pub functions: Vec<Function>,
    #[serde(default)]
    pub events: Vec<Event>,
    #[serde(default)]
    pub errors: Vec<AbiError>,
    /// Unknown/extension fields preserved.
    #[serde(default)]
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Function {
    pub name: String,
    #[serde(default)]
    pub inputs: Vec<Param>,
    #[serde(default)]
    pub outputs: Vec<Param>,
    /// If true, call is intended to transfer value along with execution. Pure metadata.
    #[serde(default)]
    pub payable: bool,
    #[serde(default)]
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Event {
    pub name: String,
    #[serde(default)]
    pub inputs: Vec<Param>,
    /// Optional anonymous flag (kept for forward-compat).
    #[serde(default)]
    pub anonymous: bool,
    #[serde(default)]
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AbiError {
    pub name: String,
    #[serde(default)]
    pub inputs: Vec<Param>,
    #[serde(default)]
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

/// Function/event parameter.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Param {
    pub name: String,
    /// Type string (e.g., "u64", "bool", "bytes", "bytes32", "string", "address", "u64[]").
    #[serde(rename = "type")]
    pub typ: String,
    /// For events only: whether this parameter is sent as an indexed topic.
    #[serde(default)]
    pub indexed: bool,
    #[serde(default)]
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

/// Parsed/normalized ABI type.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AbiType {
    Bool,
    U { bits: u16 }, // 8,16,32,64,128,256
    I { bits: u16 }, // 64,128 (commonly used)
    /// Dynamic bytes.
    Bytes,
    /// Fixed-length bytes (1..=256).
    FixedBytes(u16),
    String,
    Address,
    Array(Box<AbiType>),
}

impl AbiType {
    pub fn is_dynamic(&self) -> bool {
        match self {
            AbiType::Bytes | AbiType::String => true,
            AbiType::Array(_) => true,
            _ => false,
        }
    }
}

/// Parse a type string into `AbiType`.
pub fn parse_type(s: &str) -> Option<AbiType> {
    // Arrays: recursive `<inner>[]`
    if let Some(stripped) = s.strip_suffix("[]") {
        return parse_type(stripped).map(|t| AbiType::Array(Box::new(t)));
    }

    let t = s.trim().to_ascii_lowercase();
    match t.as_str() {
        "bool" => Some(AbiType::Bool),
        "bytes" => Some(AbiType::Bytes),
        "string" => Some(AbiType::String),
        "address" => Some(AbiType::Address),
        "u8" => Some(AbiType::U { bits: 8 }),
        "u16" => Some(AbiType::U { bits: 16 }),
        "u32" => Some(AbiType::U { bits: 32 }),
        "u64" => Some(AbiType::U { bits: 64 }),
        "u128" => Some(AbiType::U { bits: 128 }),
        "u256" => Some(AbiType::U { bits: 256 }),
        "i64" => Some(AbiType::I { bits: 64 }),
        "i128" => Some(AbiType::I { bits: 128 }),
        _ => {
            // bytes<N> (N in 1..=256)
            if let Some(nstr) = t.strip_prefix("bytes") {
                if let Ok(n) = nstr.parse::<u16>() {
                    if (1..=256).contains(&n) {
                        return Some(AbiType::FixedBytes(n));
                    }
                }
            }
            None
        }
    }
}

impl Param {
    pub fn parsed_type(&self) -> Result<AbiType> {
        parse_type(&self.typ).ok_or_else(|| Error::Abi(format!("unknown type: {}", self.typ)))
    }
}

// ---------- Validation --------------------------------------------------------

fn is_ident(s: &str) -> bool {
    let mut chars = s.chars();
    match chars.next() {
        Some(c) if c == '_' || c.is_ascii_alphabetic() => {}
        _ => return false,
    }
    chars.all(|c| c == '_' || c.is_ascii_alphanumeric())
}

impl Abi {
    /// Parse from JSON string and validate.
    pub fn from_json_str(json: &str) -> Result<Self> {
        let abi: Abi = serde_json::from_str(json)?;
        abi.validate()?;
        Ok(abi)
    }

    /// Validate structure and semantic constraints.
    pub fn validate(&self) -> Result<()> {
        if let Some(name) = &self.name {
            if !name.is_empty() && !is_ident(name) {
                return Err(Error::Abi(format!("invalid contract name: {name}")));
            }
        }

        // Uniqueness of function/event/error names
        let mut fn_names = BTreeSet::new();
        for f in &self.functions {
            f.validate(false /*is_event*/)?;
            if !fn_names.insert(f.name.to_string()) {
                return Err(Error::Abi(format!("duplicate function name: {}", f.name)));
            }
        }

        let mut ev_names = BTreeSet::new();
        for e in &self.events {
            e.validate()?;
            if !ev_names.insert(e.name.to_string()) {
                return Err(Error::Abi(format!("duplicate event name: {}", e.name)));
            }
        }

        let mut err_names = BTreeSet::new();
        for e in &self.errors {
            e.validate()?;
            if !err_names.insert(e.name.to_string()) {
                return Err(Error::Abi(format!("duplicate error name: {}", e.name)));
            }
        }

        Ok(())
    }

    /// Lookup a function by name.
    pub fn function(&self, name: &str) -> Option<&Function> {
        self.functions.iter().find(|f| f.name == name)
    }

    /// Lookup an event by name.
    pub fn event(&self, name: &str) -> Option<&Event> {
        self.events.iter().find(|e| e.name == name)
    }

    /// Lookup an error by name.
    pub fn error(&self, name: &str) -> Option<&AbiError> {
        self.errors.iter().find(|e| e.name == name)
    }
}

impl Function {
    pub fn validate(&self, is_event: bool) -> Result<()> {
        if self.name.is_empty() || !is_ident(&self.name) {
            return Err(Error::Abi(format!("invalid function name: {}", self.name)));
        }
        let mut seen = BTreeSet::new();
        for p in &self.inputs {
            if !p.name.is_empty() && !is_ident(&p.name) {
                return Err(Error::Abi(format!(
                    "invalid param name '{}' in function '{}'",
                    p.name, self.name
                )));
            }
            if !seen.insert(p.name.clone()) {
                return Err(Error::Abi(format!(
                    "duplicate param '{}' in function '{}'",
                    p.name, self.name
                )));
            }
            let pt = p.parsed_type()?;
            if is_event && p.indexed && pt.is_dynamic() {
                return Err(Error::Abi(format!(
                    "event param '{}' in '{}' cannot be indexed (dynamic type {})",
                    p.name, self.name, p.typ
                )));
            }
        }
        for (i, o) in self.outputs.iter().enumerate() {
            if !o.name.is_empty() && !is_ident(&o.name) {
                return Err(Error::Abi(format!(
                    "invalid output name '{}' (#{}) in function '{}'",
                    o.name, i, self.name
                )));
            }
            let _ = o.parsed_type()?;
            if o.indexed {
                return Err(Error::Abi(format!(
                    "output '{}' in function '{}' must not be indexed",
                    o.name, self.name
                )));
            }
        }
        Ok(())
    }
}

impl Event {
    pub fn validate(&self) -> Result<()> {
        if self.name.is_empty() || !is_ident(&self.name) {
            return Err(Error::Abi(format!("invalid event name: {}", self.name)));
        }
        // Reuse Function::validate rules with is_event=true.
        let f_like = Function {
            name: self.name.clone(),
            inputs: self.inputs.clone(),
            outputs: vec![],
            payable: false,
            extra: BTreeMap::new(),
        };
        f_like.validate(true)
    }
}

impl AbiError {
    pub fn validate(&self) -> Result<()> {
        if self.name.is_empty() || !is_ident(&self.name) {
            return Err(Error::Abi(format!("invalid error name: {}", self.name)));
        }
        let mut seen = BTreeSet::new();
        for p in &self.inputs {
            if !p.name.is_empty() && !is_ident(&p.name) {
                return Err(Error::Abi(format!(
                    "invalid error param name '{}' in '{}'",
                    p.name, self.name
                )));
            }
            if !seen.insert(p.name.clone()) {
                return Err(Error::Abi(format!(
                    "duplicate error param '{}' in '{}'",
                    p.name, self.name
                )));
            }
            let _ = p.parsed_type()?;
            if p.indexed {
                return Err(Error::Abi(format!(
                    "error param '{}' in '{}' must not be indexed",
                    p.name, self.name
                )));
            }
        }
        Ok(())
    }
}

// ---------- Convenience -------------------------------------------------------

impl Abi {
    /// Serialize pretty JSON.
    pub fn to_pretty_json(&self) -> Result<String> {
        Ok(serde_json::to_string_pretty(self)?)
    }
}

// ---------- Tests -------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_types_ok() {
        assert_eq!(parse_type("u64"), Some(AbiType::U { bits: 64 }));
        assert_eq!(parse_type("i128"), Some(AbiType::I { bits: 128 }));
        assert_eq!(parse_type("bytes"), Some(AbiType::Bytes));
        assert_eq!(parse_type("bytes32"), Some(AbiType::FixedBytes(32)));
        assert_eq!(
            parse_type("u64[]"),
            Some(AbiType::Array(Box::new(AbiType::U { bits: 64 })))
        );
        assert_eq!(parse_type("string"), Some(AbiType::String));
        assert_eq!(parse_type("address"), Some(AbiType::Address));
        assert!(parse_type("weird").is_none());
    }

    #[test]
    fn validate_happy_path() {
        let abi = Abi {
            name: Some("Counter".into()),
            functions: vec![
                Function {
                    name: "inc".into(),
                    inputs: vec![Param { name: "delta".into(), typ: "u64".into(), indexed: false, extra: BTreeMap::new() }],
                    outputs: vec![],
                    payable: false,
                    extra: BTreeMap::new(),
                },
                Function {
                    name: "get".into(),
                    inputs: vec![],
                    outputs: vec![Param { name: "value".into(), typ: "u64".into(), indexed: false, extra: BTreeMap::new() }],
                    payable: false,
                    extra: BTreeMap::new(),
                },
            ],
            events: vec![Event {
                name: "Incremented".into(),
                inputs: vec![Param { name: "by".into(), typ: "u64".into(), indexed: true, extra: BTreeMap::new() }],
                anonymous: false,
                extra: BTreeMap::new(),
            }],
            errors: vec![AbiError {
                name: "RevertReason".into(),
                inputs: vec![Param { name: "msg".into(), typ: "string".into(), indexed: false, extra: BTreeMap::new() }],
                extra: BTreeMap::new(),
            }],
            extra: BTreeMap::new(),
        };
        assert!(abi.validate().is_ok());
    }

    #[test]
    fn reject_duplicate_function() {
        let abi = Abi {
            name: None,
            functions: vec![
                Function { name: "f".into(), inputs: vec![], outputs: vec![], payable: false, extra: BTreeMap::new() },
                Function { name: "f".into(), inputs: vec![], outputs: vec![], payable: false, extra: BTreeMap::new() },
            ],
            events: vec![],
            errors: vec![],
            extra: BTreeMap::new(),
        };
        assert!(abi.validate().is_err());
    }

    #[test]
    fn reject_indexed_dynamic_event_param() {
        let abi = Abi {
            name: None,
            functions: vec![],
            events: vec![Event {
                name: "E".into(),
                inputs: vec![Param { name: "msg".into(), typ: "string".into(), indexed: true, extra: BTreeMap::new() }],
                anonymous: false,
                extra: BTreeMap::new(),
            }],
            errors: vec![],
            extra: BTreeMap::new(),
        };
        assert!(abi.validate().is_err());
    }
}
