use core::fmt;
use thiserror::Error;

/// Common result alias for the SDK.
pub type Result<T, E = Error> = std::result::Result<T, E>;

/// Structured JSON-RPC error object.
#[derive(Debug, Clone)]
pub struct RpcErrorObj {
    pub code: i64,
    pub message: String,
    pub data: Option<serde_json::Value>,
}

impl RpcErrorObj {
    pub fn new(code: i64, message: impl Into<String>, data: Option<serde_json::Value>) -> Self {
        Self { code, message: message.into(), data }
    }
}

impl fmt::Display for RpcErrorObj {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if let Some(d) = &self.data {
            write!(f, "RPC error {}: {} (data={})", self.code, self.message, d)
        } else {
            write!(f, "RPC error {}: {}", self.code, self.message)
        }
    }
}

/// Top-level SDK error.
///
/// Variants are intentionally broad and stable. Specific submodules may expose their
/// own detail enums if needed, but they should convert into this error for public APIs.
#[non_exhaustive]
#[derive(Debug, Error)]
pub enum Error {
    // ---- Transport / IO ----------------------------------------------------
    /// Network error (request building, connection, DNS, etc.).
    #[error("network error: {0}")]
    Network(String),

    /// HTTP status error (non-2xx).
    #[error("http status {status}: {body}")]
    HttpStatus { status: u16, body: String },

    /// WebSocket error (handshake, protocol, I/O).
    #[error("websocket error: {0}")]
    WebSocket(String),

    /// Timeout reached while waiting for a response or receipt.
    #[error("timeout: {0}")]
    Timeout(&'static str),

    /// Retry policy exhausted.
    #[error("retry attempts exhausted")]
    RetryExhausted,

    /// Generic IO error.
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    /// URL parse error.
    #[error("url parse error: {0}")]
    Url(#[from] url::ParseError),

    // ---- Encoding / Decoding ----------------------------------------------
    /// JSON (de)serialization error.
    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),

    /// CBOR encode error.
    #[error("cbor encode error: {0}")]
    CborEncode(String),

    /// CBOR decode error.
    #[error("cbor decode error: {0}")]
    CborDecode(String),

    /// Bech32/Bech32m address codec error.
    #[error("bech32 error: {0}")]
    Bech32(#[from] bech32::Error),

    /// Hex decoding error.
    #[error("hex decode error: {0}")]
    Hex(#[from] hex::FromHexError),

    // ---- RPC / Protocol ----------------------------------------------------
    /// JSON-RPC error response with structured details.
    #[error("{0}")]
    Rpc(#[from] RpcErrorObj),

    /// RPC response had an unexpected shape.
    #[error("malformed rpc response: {0}")]
    RpcResponse(String),

    /// Chain ID mismatch between request and node.
    #[error("chain id mismatch: expected {expected}, got {got}")]
    ChainIdMismatch { expected: u64, got: u64 },

    /// Requested object not found.
    #[error("not found: {0}")]
    NotFound(&'static str),

    /// Waiting for transaction receipt timed out.
    #[error("receipt not found within timeout for tx {0}")]
    ReceiptTimeout(String),

    // ---- Wallet / Crypto ---------------------------------------------------
    /// Signer/crypto error (non-PQ specific).
    #[error("signer error: {0}")]
    Signer(String),

    /// Keystore error (load/save/unlock).
    #[error("keystore error: {0}")]
    Keystore(String),

    /// Address error (invalid length/prefix/alg_id).
    #[error("address error: {0}")]
    Address(String),

    /// Post-quantum signer not compiled in or unavailable.
    #[error("pq signer unavailable (feature 'pq' not enabled or backend missing)")]
    PqUnavailable,

    // ---- ABI / Contracts ---------------------------------------------------
    /// ABI validation or encoding/decoding error.
    #[error("abi error: {0}")]
    Abi(String),

    /// Event/topic decoding error.
    #[error("event decode error: {0}")]
    Events(String),

    // ---- Domain services ---------------------------------------------------
    /// Data Availability client error.
    #[error("da client error: {0}")]
    Da(String),

    /// AI Compute Fund client error.
    #[error("aicf client error: {0}")]
    Aicf(String),

    /// Randomness client error.
    #[error("randomness client error: {0}")]
    Randomness(String),

    // ---- Misc --------------------------------------------------------------
    /// Feature is not available in current build/target.
    #[error("feature not available on this target")]
    FeatureUnavailable,

    /// Invalid parameters for a call.
    #[error("invalid parameters: {0}")]
    InvalidParams(&'static str),
}

impl Error {
    /// Whether this error is likely transient and safe to retry.
    pub fn is_retryable(&self) -> bool {
        use Error::*;
        match self {
            Network(_) | WebSocket(_) | Timeout(_) => true,
            HttpStatus { status, .. } => (500..=599).contains(status) || *status == 429,
            Rpc(e) => {
                // Heuristics: JSON-RPC codes < -32000 are server internal;
                // -32603 (Internal error) generally retryable; -320xx also often server-side.
                e.code == -32603 || (e.code <= -32000 && e.code >= -32099)
            }
            RetryExhausted
            | Io(_)
            | Url(_)
            | Json(_)
            | CborEncode(_)
            | CborDecode(_)
            | Bech32(_)
            | Hex(_)
            | RpcResponse(_)
            | ChainIdMismatch { .. }
            | NotFound(_)
            | ReceiptTimeout(_)
            | Signer(_)
            | Keystore(_)
            | Address(_)
            | PqUnavailable
            | Abi(_)
            | Events(_)
            | Da(_)
            | Aicf(_)
            | Randomness(_)
            | FeatureUnavailable
            | InvalidParams(_) => false,
        }
    }
}

// ---- Conversions from common backends ---------------------------------------

#[cfg(feature = "native")]
impl From<reqwest::Error> for Error {
    fn from(e: reqwest::Error) -> Self {
        if e.is_timeout() {
            Error::Timeout("http")
        } else if let Some(status) = e.status() {
            Error::HttpStatus { status: status.as_u16(), body: e.to_string() }
        } else {
            Error::Network(e.to_string())
        }
    }
}

#[cfg(feature = "native")]
impl From<tungstenite::Error> for Error {
    fn from(e: tungstenite::Error) -> Self {
        use tungstenite::error::ProtocolError;
        match e {
            tungstenite::Error::Io(ioe) => Error::Io(ioe),
            tungstenite::Error::Tls(_) => Error::WebSocket("tls".into()),
            tungstenite::Error::Capacity(_) => Error::WebSocket("capacity".into()),
            tungstenite::Error::Protocol(ProtocolError::ResetWithoutClosingHandshake) => {
                Error::WebSocket("closed".into())
            }
            other => Error::WebSocket(other.to_string()),
        }
    }
}

#[cfg(feature = "wasm")]
impl From<gloo_net::Error> for Error {
    fn from(e: gloo_net::Error) -> Self {
        // gloo-net wraps JS errors without rich kinds; treat as network.
        Error::Network(e.to_string())
    }
}

// Ciborium errors carry generic type params; map to strings.
impl<T: fmt::Display> From<ciborium::ser::Error<T>> for Error {
    fn from(e: ciborium::ser::Error<T>) -> Self {
        Error::CborEncode(e.to_string())
    }
}

impl<T: fmt::Display> From<ciborium::de::Error<T>> for Error {
    fn from(e: ciborium::de::Error<T>) -> Self {
        Error::CborDecode(e.to_string())
    }
}

// Allow easy conversion from our RpcErrorObj by-value.
impl From<RpcErrorObj> for Error {
    fn from(e: RpcErrorObj) -> Self {
        Error::Rpc(e)
    }
}
