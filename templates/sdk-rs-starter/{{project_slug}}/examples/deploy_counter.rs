// A small end-to-end example that deploys a "counter" contract package.
//
// Usage:
//   ANIMICA_RPC_URL=http://127.0.0.1:8545 ANIMICA_CHAIN_ID=1337 \
//   cargo run --example deploy_counter -- \
//     --manifest ./contracts/counter/manifest.json \
//     --code ./contracts/counter/code.bin \
//     [--salt 0xdeadbeef]
//
// Notes:
// - If --code is omitted, this script will look for a hex-encoded "code"/"bytecode"
//   field inside the manifest JSON.
// - The script calls the generic JSON-RPC method "contracts.deployPackage" and tries
//   to handle a few common response shapes (either a direct {address,...} object,
//   or a {txHash,...} that requires polling for a receipt).
//
// Environment:
//   ANIMICA_RPC_URL / RPC_URL        - JSON-RPC endpoint
//   ANIMICA_CHAIN_ID / CHAIN_ID      - chain id (integer)
//   ANIMICA_TIMEOUT_SECS             - optional request timeout (default 20s)
//
// Dependencies (already present in this starter template):
//   anyhow, serde_json, tracing, tracing-subscriber, tokio, dotenvy
//
// Adjust the method name or params to match your node's API if it differs.

use std::{fs, path::PathBuf, time::Duration};

use anyhow::{anyhow, Context, Result};
use serde_json::{json, Value};
use tracing::{error, info};
use {{crate_name}}::prelude::*;

#[tokio::main]
async fn main() -> Result<()> {
    init_tracing();

    let args = Args::parse_from_env()?;
    info!("manifest = {}", args.manifest.display());
    if let Some(code) = &args.code {
        info!("code     = {}", code.display());
    }
    if let Some(salt) = &args.salt {
        info!("salt     = {salt}");
    }

    // Load config from env & build client
    let cfg = Config::from_env()?;
    let client = NodeClient::new(cfg.clone())?;
    client.assert_chain_id().await?;

    // Read manifest JSON
    let manifest_raw = fs::read_to_string(&args.manifest)
        .with_context(|| format!("reading manifest {}", args.manifest.display()))?;
    let mut manifest: Value =
        serde_json::from_str(&manifest_raw).context("parsing manifest JSON")?;

    // Obtain code bytes either from --code path or from manifest.{code|bytecode}
    let code_bytes = if let Some(code_path) = &args.code {
        fs::read(code_path)
            .with_context(|| format!("reading code bytes {}", code_path.display()))?
    } else {
        extract_code_from_manifest(&manifest)?
    };
    let code_hex = format!("0x{}", hex(&code_bytes));

    // Optionally pass a deployment salt (for predictable/proxy-ish addresses)
    let mut options = serde_json::Map::new();
    if let Some(salt) = args.salt {
        options.insert("salt".to_string(), Value::String(salt));
    }
    // Optional gas/fee knobs could go here as well, if your node supports them.

    // Compose params: [manifest, code_hex, {options}]
    let params = if options.is_empty() {
        json!([manifest, code_hex])
    } else {
        json!([manifest, code_hex, Value::Object(options)])
    };

    info!("submitting deployPackage...");
    let res: Value = client
        .call("contracts.deployPackage", params)
        .await
        .context("contracts.deployPackage failed")?;

    // Try to interpret the response:
    // 1) Direct address in result (e.g. {"address": "0x..."} or {"contractAddress": "0x..."})
    if let Some(addr) = res.get("address").and_then(|v| v.as_str())
        .or_else(|| res.get("contractAddress").and_then(|v| v.as_str()))
    {
        println!("✅ Deployed at address: {addr}");
        return Ok(());
    }

    // 2) A tx hash we should wait on (e.g. {"txHash":"0x..."} or a bare string)
    if let Some(tx_hash) = res.get("txHash").and_then(|v| v.as_str())
        .or_else(|| res.as_str())
    {
        println!("submitted deploy tx: {tx_hash}");
        let receipt = client
            .await_receipt(tx_hash, Duration::from_secs(60), Duration::from_secs(1))
            .await
            .context("waiting for deploy receipt")?;

        // Best-effort: receive address from receipt if present
        if let Some(addr) = find_contract_address_in_receipt(&receipt) {
            println!("✅ Deployed at address: {addr}");
        } else {
            println!("ℹ️  Deploy receipt received, but no contract address was found.\nReceipt: {receipt:?}");
        }
        return Ok(());
    }

    // 3) Fallback: show the raw JSON for debugging
    error!("Unrecognized deploy response: {res}");
    Err(anyhow!("unrecognized deploy response, see logs"))
}

/// Minimal, hand-rolled arg parsing to keep the example dependency-light.
struct Args {
    manifest: PathBuf,
    code: Option<PathBuf>,
    salt: Option<String>,
}

impl Args {
    fn parse_from_env() -> Result<Self> {
        let mut it = std::env::args().skip(1);
        let mut manifest = None;
        let mut code = None;
        let mut salt = None;

        while let Some(flag) = it.next() {
            match flag.as_str() {
                "--manifest" => manifest = it.next().map(Into::into),
                "--code" => code = it.next().map(Into::into),
                "--salt" => salt = it.next(),
                f if f == "-h" || f == "--help" => {
                    print_help();
                    std::process::exit(0);
                }
                other => return Err(anyhow!("unknown flag: {other}")),
            }
        }

        let manifest = manifest.ok_or_else(|| anyhow!("--manifest <path> is required"))?;
        Ok(Self { manifest, code, salt })
    }
}

fn print_help() {
    eprintln!(
        "Usage:
  deploy_counter --manifest <path> [--code <path>] [--salt <hex>]

Flags:
  --manifest <path>   Path to manifest JSON describing the contract package
  --code <path>       Optional path to raw code bytes (if not in manifest)
  --salt <hex>        Optional deployment salt (0x-hex), for predictable address

Env:
  ANIMICA_RPC_URL / RPC_URL
  ANIMICA_CHAIN_ID / CHAIN_ID
  ANIMICA_TIMEOUT_SECS"
    );
}

/// Extract hex- or base64-encoded code bytes from a manifest JSON.
///
/// Supported shapes (best effort):
/// - { "code": "0x...." } or { "bytecode": "0x...." }
/// - { "package": { "code": "0x...." } }
/// - Same keys but without 0x -> interpreted as hex
/// - If a string looks like base64, we attempt base64 decode.
///
/// Returns an error if nothing workable is found.
fn extract_code_from_manifest(manifest: &Value) -> Result<Vec<u8>> {
    fn try_decode(s: &str) -> Option<Vec<u8>> {
        let s_trim = s.trim();
        // Try 0x-hex or plain hex first
        let hex_str = s_trim.strip_prefix("0x").unwrap_or(s_trim);
        if is_even_len_hex(hex_str) && hex::decode(hex_str).is_ok() {
            return Some(hex::decode(hex_str).ok()?);
        }
        // Fallback: base64 (URL-safe not considered here)
        base64::decode(s_trim).ok()
    }

    let candidates = [
        manifest.get("code"),
        manifest.get("bytecode"),
        manifest.get("package").and_then(|p| p.get("code")),
        manifest.get("package").and_then(|p| p.get("bytecode")),
    ];

    for cand in candidates.into_iter().flatten() {
        if let Some(s) = cand.as_str() {
            if let Some(bytes) = try_decode(s) {
                return Ok(bytes);
            }
        }
    }

    Err(anyhow!(
        "manifest does not contain a decodable 'code'/'bytecode' field; \
         pass --code <path> explicitly"
    ))
}

/// Poor man's hex predicate: even length and only [0-9a-fA-F].
fn is_even_len_hex(s: &str) -> bool {
    let bytes_ok = s
        .bytes()
        .all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f' | b'A'..=b'F'));
    bytes_ok && (s.len() % 2 == 0)
}

/// Find a deployed address in a generic receipt payload.
///
/// Heuristics used (best effort):
/// - top-level `contractAddress` string
/// - first log with `event` == "ContractDeployed" carrying `address`
/// - `createdAddress` string
fn find_contract_address_in_receipt(receipt: &types::Receipt) -> Option<String> {
    // The Receipt type may or may not be flexible; if it has an extensions/extra field,
    // search it. Otherwise, fallback to stringification-based scan.
    let value = serde_json::to_value(receipt).ok()?;

    // 1. contractAddress / createdAddress
    if let Some(addr) = value
        .get("contractAddress")
        .and_then(|v| v.as_str())
        .or_else(|| value.get("createdAddress").and_then(|v| v.as_str()))
    {
        return Some(addr.to_string());
    }

    // 2. logs[*] with event "ContractDeployed"
    if let Some(logs) = value.get("logs").and_then(|v| v.as_array()) {
        for log in logs {
            if log.get("event").and_then(|v| v.as_str()) == Some("ContractDeployed") {
                if let Some(addr) = log.get("address").and_then(|v| v.as_str()) {
                    return Some(addr.to_string());
                }
            }
        }
    }

    None
}

/// Initialize logging/tracing subscriber with a simple env filter.
fn init_tracing() {
    let _ = dotenvy::dotenv();
    let env_filter = std::env::var("RUST_LOG").unwrap_or_else(|_| "info,animica_sdk=info".into());
    let _ = tracing_subscriber::fmt()
        .with_env_filter(env_filter)
        .with_target(false)
        .try_init();
}

/// Local minimal hex encoder to avoid pulling extra crates in the example.
/// (We also include a decode-only check via the `hex` crate inside `extract_code_from_manifest`.)
fn hex(bytes: &[u8]) -> String {
    const LUT: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        out.push(LUT[(b >> 4) as usize] as char);
        out.push(LUT[(b & 0x0f) as usize] as char);
    }
    out
}

// --- small, scoped third-party helpers --------------------------------------

mod base64 {
    // Re-export `base64` decode behind a tiny wrapper to keep main code tidy.
    pub fn decode(s: &str) -> Result<Vec<u8>, base64::DecodeError> {
        base64::engine::general_purpose::STANDARD.decode(s)
    }
}

mod hex {
    pub use hex::decode;
}
