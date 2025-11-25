//! Randomness (beacon) client for Animica nodes.
//!
//! RPC surface (served by the node):
//! - rand.getParams() -> Params
//! - rand.getRound() -> Round info (current round / phase)
//! - rand.commit({salt,payload}) -> Commit receipt
//! - rand.reveal({salt,payload}) -> Reveal receipt
//! - rand.getBeacon([roundId?]) -> Beacon for current or specific round
//! - rand.getHistory([offset?, limit?]) -> List of recent beacons (optional pagination)
//!
//! All methods below prefer typed structs with forward-compatible `[flatten]` extra fields.
//! Binary parameters use hex with 0x prefix as per node conventions.

use crate::error::{Error, Result};
use crate::rpc::http::JsonRpcClient;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value as JsonValue};
use std::collections::BTreeMap;

/// Chain randomness parameters. All fields are optional to be forward compatible.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RandParams {
    /// Nominal seconds per round (commit -> reveal -> finalize).
    #[serde(default)]
    pub round_seconds: Option<u64>,
    /// Grace window (seconds) for reveals after commit window closes.
    #[serde(default)]
    pub reveal_grace_seconds: Option<u64>,
    /// VDF iteration target for verifier security level.
    #[serde(default)]
    pub vdf_iterations: Option<u64>,
    /// Optional modulus bits / profile identifier, if exposed.
    #[serde(default)]
    pub vdf_security_bits: Option<u32>,
    /// Extra future fields.
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

/// Current round info / schedule snapshot.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Round {
    /// Round identifier (aliases tolerated).
    #[serde(alias = "id", alias = "roundId")]
    pub round_id: u64,
    /// Phase label, e.g. "Commit", "Reveal", "Finalize".
    #[serde(default)]
    pub phase: Option<String>,
    /// RFC3339 timestamps if provided by the server.
    #[serde(default)]
    pub commit_open_at: Option<String>,
    #[serde(default)]
    pub commit_close_at: Option<String>,
    #[serde(default)]
    pub reveal_open_at: Option<String>,
    #[serde(default)]
    pub reveal_close_at: Option<String>,
    /// Extra future fields.
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

/// Receipt/result returned by `rand.commit`.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CommitReceipt {
    #[serde(alias = "id", alias = "roundId")]
    pub round_id: u64,
    /// Commitment hash or record id.
    #[serde(default)]
    pub commit_hash: Option<String>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

/// Receipt/result returned by `rand.reveal`.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct RevealReceipt {
    #[serde(alias = "id", alias = "roundId")]
    pub round_id: u64,
    /// Whether the reveal was accepted this round.
    #[serde(default)]
    pub accepted: Option<bool>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

/// Finalized beacon output (optionally includes proofs).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Beacon {
    #[serde(alias = "id", alias = "roundId")]
    pub round_id: u64,
    /// Beacon output digest (0x-hex).
    pub output: String,
    /// Optional Wesolowski proof or reference.
    #[serde(default)]
    pub vdf_proof: Option<JsonValue>,
    /// Optional compact/light proof object for light clients.
    #[serde(default)]
    pub light_proof: Option<JsonValue>,
    /// Optional seed / aggregate transcript fields.
    #[serde(default)]
    pub seed: Option<String>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

/// `rand.getHistory` envelope if the server returns pagination metadata.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct History {
    pub items: Vec<Beacon>,
    #[serde(default)]
    pub next_offset: Option<u64>,
    #[serde(default)]
    pub more: Option<bool>,
    #[serde(flatten)]
    pub extra: BTreeMap<String, JsonValue>,
}

/// Randomness JSON-RPC client.
#[derive(Clone)]
pub struct RandomnessClient {
    rpc: JsonRpcClient,
}

impl RandomnessClient {
    /// Create a new client against a node RPC URL, e.g. `http://127.0.0.1:8545`.
    pub fn new(rpc_url: &str) -> Result<Self> {
        Ok(Self {
            rpc: JsonRpcClient::new(rpc_url)?,
        })
    }

    /// Fetch chain randomness params.
    pub async fn get_params(&self) -> Result<RandParams> {
        self.rpc
            .call("rand.getParams", json!([]))
            .await
            .map_err(|e| Error::Rpc(format!("rand.getParams: {e}")))
    }

    /// Fetch current round info.
    pub async fn get_round(&self) -> Result<Round> {
        self.rpc
            .call("rand.getRound", json!([]))
            .await
            .map_err(|e| Error::Rpc(format!("rand.getRound: {e}")))
    }

    /// Submit a commitment. `salt` and `payload` are raw bytes; will be hex-encoded with 0x prefix.
    pub async fn commit(&self, salt: &[u8], payload: &[u8]) -> Result<CommitReceipt> {
        let params = json!([{
            "salt": format!("0x{}", hex::encode(salt)),
            "payload": format!("0x{}", hex::encode(payload)),
        }]);
        self.rpc
            .call("rand.commit", params)
            .await
            .map_err(|e| Error::Rpc(format!("rand.commit: {e}")))
    }

    /// Reveal a prior commitment. Same salt/payload as used for `commit`.
    pub async fn reveal(&self, salt: &[u8], payload: &[u8]) -> Result<RevealReceipt> {
        let params = json!([{
            "salt": format!("0x{}", hex::encode(salt)),
            "payload": format!("0x{}", hex::encode(payload)),
        }]);
        self.rpc
            .call("rand.reveal", params)
            .await
            .map_err(|e| Error::Rpc(format!("rand.reveal: {e}")))
    }

    /// Get the latest finalized beacon (no arguments).
    pub async fn get_beacon_latest(&self) -> Result<Beacon> {
        self.rpc
            .call("rand.getBeacon", json!([]))
            .await
            .map_err(|e| Error::Rpc(format!("rand.getBeacon(latest): {e}")))
    }

    /// Get the beacon for a specific round id.
    pub async fn get_beacon(&self, round_id: u64) -> Result<Beacon> {
        self.rpc
            .call("rand.getBeacon", json!([round_id]))
            .await
            .map_err(|e| Error::Rpc(format!("rand.getBeacon({round_id}): {e}")))
    }

    /// Get recent beacon history. Some deployments return `History { items, nextOffset }`,
    /// others may return a bare array. This helper normalizes into `History`.
    pub async fn get_history(&self, offset: Option<u64>, limit: Option<u64>) -> Result<History> {
        let params = match (offset, limit) {
            (Some(o), Some(l)) => json!([o, l]),
            (Some(o), None) => json!([o]),
            _ => json!([]),
        };

        let raw: JsonValue = self
            .rpc
            .call("rand.getHistory", params)
            .await
            .map_err(|e| Error::Rpc(format!("rand.getHistory: {e}")))?;

        // Try structured History first.
        if let Ok(h) = serde_json::from_value::<History>(raw.clone()) {
            return Ok(h);
        }
        // Otherwise accept a bare array of Beacon.
        if let Ok(items) = serde_json::from_value::<Vec<Beacon>>(raw) {
            return Ok(History {
                items,
                next_offset: None,
                more: None,
                extra: BTreeMap::new(),
            });
        }
        Err(Error::Rpc("rand.getHistory: unexpected response shape".into()))
    }
}

// ---------------------------------- Tests ------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn beacon_roundtrip_allows_extras() {
        let s = r#"{
            "roundId": 123,
            "output": "0xdeadbeef",
            "vdfProof": {"pi":"0x01","y":"0x02"},
            "lightProof": {"hash":"0x03"},
            "seed": "0xabc",
            "foo": "bar"
        }"#;
        let b: Beacon = serde_json::from_str(s).unwrap();
        assert_eq!(b.round_id, 123);
        assert_eq!(b.output, "0xdeadbeef");
        assert!(b.extra.contains_key("foo"));
    }

    #[test]
    fn params_roundtrip_optional() {
        let p: RandParams = serde_json::from_value(json!({
            "roundSeconds": 30,
            "revealGraceSeconds": 5,
            "vdfIterations": 1_000_000
        }))
        .unwrap();
        assert_eq!(p.round_seconds, Some(30));
        assert_eq!(p.reveal_grace_seconds, Some(5));
        assert_eq!(p.vdf_iterations, Some(1_000_000));
    }

    #[test]
    fn history_normalization() {
        // Bare array
        let raw = json!([
            {"roundId": 1, "output":"0x01"},
            {"roundId": 2, "output":"0x02"}
        ]);
        let items: Vec<Beacon> = serde_json::from_value(raw).unwrap();
        assert_eq!(items.len(), 2);
    }

    #[test]
    fn commit_hex_prep() {
        let salt = [0xAA, 0xBB];
        let payload = [0x01, 0xFF];
        let params = json!([{
            "salt": format!("0x{}", hex::encode(&salt)),
            "payload": format!("0x{}", hex::encode(&payload))
        }]);
        assert_eq!(params[0]["salt"], "0xaabb");
        assert_eq!(params[0]["payload"], "0x01ff");
    }

    #[tokio::test]
    async fn construct_client() {
        let _c = RandomnessClient::new("http://localhost:8545").unwrap();
    }
}
