//! Minimal, robust JSON-RPC HTTP client with retries (reqwest).
//!
//! Features:
//! - Async `reqwest` client with sane defaults (timeouts, UA).
//! - Exponential backoff with jitter for transient failures (5xx/429/timeouts).
//! - Typed single-call API and convenient raw/batch helpers.
//! - Optional bearer auth & custom headers.
//!
//! This client is transport-only. It does not interpret chain semantics.

use crate::error::{Error, Result};
use reqwest::{header, Client, StatusCode, Url};
use serde::{de::DeserializeOwned, Serialize};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

/// JSON-RPC 2.0 request envelope.
#[derive(Debug, Serialize)]
struct RpcRequest<'a> {
    jsonrpc: &'static str,
    id: u64,
    method: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    params: Option<Value>,
}

/// JSON-RPC 2.0 response envelope.
#[derive(Debug, Deserialize)]
struct RpcResponse<T> {
    jsonrpc: String,
    id: Value,
    #[serde(default)]
    result: Option<T>,
    #[serde(default)]
    error: Option<RpcErrorObj>,
}

#[derive(Debug, Deserialize)]
struct RpcErrorObj {
    code: i64,
    message: String,
    #[serde(default)]
    data: Option<Value>,
}

/// Builder for [`HttpClient`].
#[derive(Clone, Debug)]
pub struct HttpClientBuilder {
    endpoint: Url,
    timeout: Duration,
    connect_timeout: Duration,
    max_retries: u32,
    retry_base: Duration,
    default_headers: header::HeaderMap,
    user_agent: Option<String>,
}

impl HttpClientBuilder {
    pub fn new(endpoint: Url) -> Self {
        Self {
            endpoint,
            timeout: Duration::from_secs(20),
            connect_timeout: Duration::from_secs(10),
            max_retries: 3,
            retry_base: Duration::from_millis(250),
            default_headers: header::HeaderMap::new(),
            user_agent: None,
        }
    }

    pub fn from_str(endpoint: &str) -> Result<Self> {
        Ok(Self::new(endpoint.parse::<Url>().map_err(|e| Error::Transport(format!("bad URL: {e}")))?))
    }

    pub fn timeout(mut self, timeout: Duration) -> Self {
        self.timeout = timeout;
        self
    }

    pub fn connect_timeout(mut self, timeout: Duration) -> Self {
        self.connect_timeout = timeout;
        self
    }

    pub fn max_retries(mut self, retries: u32) -> Self {
        self.max_retries = retries;
        self
    }

    pub fn retry_base(mut self, base: Duration) -> Self {
        self.retry_base = base;
        self
    }

    pub fn bearer_auth(mut self, token: &str) -> Self {
        let value = header::HeaderValue::from_str(&format!("Bearer {token}"))
            .unwrap_or_else(|_| header::HeaderValue::from_static("Bearer INVALID"));
        self.default_headers.insert(header::AUTHORIZATION, value);
        self
    }

    pub fn header(mut self, key: header::HeaderName, value: header::HeaderValue) -> Self {
        self.default_headers.insert(key, value);
        self
    }

    pub fn user_agent(mut self, ua: &str) -> Self {
        self.user_agent = Some(ua.to_owned());
        self
    }

    pub fn build(self) -> Result<HttpClient> {
        let mut headers = self.default_headers.clone();
        headers.entry(header::CONTENT_TYPE).or_insert(header::HeaderValue::from_static("application/json"));
        if let Some(ua) = self.user_agent {
            headers
                .entry(header::USER_AGENT)
                .or_insert(header::HeaderValue::from_str(&ua).unwrap_or_else(|_| header::HeaderValue::from_static("animica-rust-sdk")));
        } else {
            headers
                .entry(header::USER_AGENT)
                .or_insert(header::HeaderValue::from_static("animica-rust-sdk"));
        }

        let client = Client::builder()
            .default_headers(headers)
            .connect_timeout(self.connect_timeout)
            .timeout(self.timeout)
            .pool_idle_timeout(Duration::from_secs(30))
            .tcp_nodelay(true)
            .build()
            .map_err(|e| Error::Transport(format!("reqwest build: {e}")))?;

        Ok(HttpClient {
            endpoint: self.endpoint,
            client,
            max_retries: self.max_retries,
            retry_base: self.retry_base,
            id: AtomicU64::new(1),
        })
    }
}

/// Async JSON-RPC HTTP client.
#[derive(Clone)]
pub struct HttpClient {
    endpoint: Url,
    client: Client,
    max_retries: u32,
    retry_base: Duration,
    id: AtomicU64,
}

impl std::fmt::Debug for HttpClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("HttpClient")
            .field("endpoint", &self.endpoint)
            .field("max_retries", &self.max_retries)
            .finish()
    }
}

impl HttpClient {
    /// Quick constructor with defaults.
    pub fn new(endpoint: &str) -> Result<Self> {
        HttpClientBuilder::from_str(endpoint)?.build()
    }

    /// Create a builder for custom configuration.
    pub fn builder(endpoint: &str) -> Result<HttpClientBuilder> {
        HttpClientBuilder::from_str(endpoint)
    }

    /// Perform a typed JSON-RPC call with automatic retries on transient errors.
    pub async fn call<T, P>(&self, method: &str, params: P) -> Result<T>
    where
        T: DeserializeOwned,
        P: Serialize,
    {
        let params_value = Some(serde_json::to_value(params).map_err(|e| Error::Serde(format!("params: {e}")))?);
        self.call_value::<T>(method, params_value).await
    }

    /// Same as [`call`] but takes pre-built `serde_json::Value` for params (or `None`).
    pub async fn call_value<T>(&self, method: &str, params: Option<Value>) -> Result<T>
    where
        T: DeserializeOwned,
    {
        let id = self.next_id();
        let req = RpcRequest {
            jsonrpc: "2.0",
            id,
            method,
            params,
        };
        let body = serde_json::to_vec(&req).map_err(|e| Error::Serde(format!("encode request: {e}")))?;
        // Retry loop
        let mut last_err: Option<Error> = None;
        for attempt in 0..=self.max_retries {
            match self.try_send::<T>(&body).await {
                Ok(v) => return Ok(v),
                Err(e) => {
                    if !self.should_retry(&e) || attempt == self.max_retries {
                        return Err(e);
                    }
                    last_err = Some(e);
                    self.sleep_backoff(attempt).await;
                }
            }
        }
        Err(last_err.unwrap_or_else(|| Error::Transport("unreachable retry loop".into())))
    }

    /// Perform a **raw** call returning the untyped `serde_json::Value` result.
    pub async fn call_raw(&self, method: &str, params: Option<Value>) -> Result<Value> {
        self.call_value::<Value>(method, params).await
    }

    /// Execute a JSON-RPC batch. Each item is `(method, params)`.
    /// Returns a vector of results **ordered by request id**, not input order (JSON-RPC spec).
    pub async fn batch(&self, calls: Vec<(&str, Option<Value>)>) -> Result<Vec<Result<Value>>> {
        if calls.is_empty() {
            return Ok(vec![]);
        }
        // Build batch with strictly increasing ids.
        let mut next = self.next_id();
        let reqs: Vec<RpcRequest<'_>> = calls
            .iter()
            .map(|(m, p)| {
                let r = RpcRequest {
                    jsonrpc: "2.0",
                    id: next,
                    method: *m,
                    params: p.clone(),
                };
                next = next.saturating_add(1);
                r
            })
            .collect();

        let body = serde_json::to_vec(&reqs).map_err(|e| Error::Serde(format!("encode batch: {e}")))?;

        // Retry loop
        let mut last_err: Option<Error> = None;
        for attempt in 0..=self.max_retries {
            match self.try_send_batch(&body).await {
                Ok(v) => return Ok(v),
                Err(e) => {
                    if !self.should_retry(&e) || attempt == self.max_retries {
                        return Err(e);
                    }
                    last_err = Some(e);
                    self.sleep_backoff(attempt).await;
                }
            }
        }
        Err(last_err.unwrap_or_else(|| Error::Transport("unreachable retry loop".into())))
    }

    // --------------------------- internals ----------------------------------

    fn next_id(&self) -> u64 {
        self.id.fetch_add(1, Ordering::Relaxed)
    }

    async fn try_send<T>(&self, body: &[u8]) -> Result<T>
    where
        T: DeserializeOwned,
    {
        let resp = self
            .client
            .post(self.endpoint.clone())
            .body(body.to_vec())
            .send()
            .await
            .map_err(|e| Error::Transport(format!("send: {e}")))?;

        let status = resp.status();
        let bytes = resp.bytes().await.map_err(|e| Error::Transport(format!("read body: {e}")))?;
        if !status.is_success() {
            return Err(http_status_error(status, &bytes));
        }

        let parsed: RpcResponse<T> = serde_json::from_slice(&bytes)
            .map_err(|e| Error::Serde(format!("decode rpc response: {e}; body={}", truncate_body(&bytes))))?;

        if let Some(err) = parsed.error {
            return Err(Error::Rpc(err.code, if let Some(data) = err.data {
                format!("{} | data={}", err.message, data)
            } else {
                err.message
            }));
        }
        parsed
            .result
            .ok_or_else(|| Error::Rpc(-32603, "missing result and error".into()))
    }

    async fn try_send_batch(&self, body: &[u8]) -> Result<Vec<Result<Value>>> {
        let resp = self
            .client
            .post(self.endpoint.clone())
            .body(body.to_vec())
            .send()
            .await
            .map_err(|e| Error::Transport(format!("send batch: {e}")))?;

        let status = resp.status();
        let bytes = resp.bytes().await.map_err(|e| Error::Transport(format!("read body: {e}")))?;
        if !status.is_success() {
            return Err(http_status_error(status, &bytes));
        }

        let parsed: Vec<RpcResponse<Value>> = serde_json::from_slice(&bytes)
            .map_err(|e| Error::Serde(format!("decode batch response: {e}; body={}", truncate_body(&bytes))))?;

        // Map to ordered results by id ascending (as many servers already do).
        let mut items: Vec<(u64, Result<Value>)> = Vec::with_capacity(parsed.len());
        for r in parsed {
            // best-effort id → u64
            let id_num = r
                .id
                .as_u64()
                .or_else(|| r.id.as_str().and_then(|s| s.parse::<u64>().ok()))
                .unwrap_or(0);

            let res = if let Some(err) = r.error {
                Err(Error::Rpc(
                    err.code,
                    if let Some(data) = err.data {
                        format!("{} | data={}", err.message, data)
                    } else {
                        err.message
                    },
                ))
            } else if let Some(val) = r.result {
                Ok(val)
            } else {
                Err(Error::Rpc(-32603, "missing result and error".into()))
            };
            items.push((id_num, res));
        }
        items.sort_by_key(|(id, _)| *id);
        Ok(items.into_iter().map(|(_, r)| r).collect())
    }

    fn should_retry(&self, err: &Error) -> bool {
        match err {
            Error::Transport(msg) => {
                // Rough heuristic: timeouts, DNS/connect, temporary network issues
                contains_any(msg, &["timeout", "timed out", "connect", "tls", "closed", "reset", "EOF"])
            }
            Error::Http(status, _msg) => {
                // Retry on 408, 425, 429, 5xx
                *status == StatusCode::REQUEST_TIMEOUT
                    || *status == StatusCode::TOO_EARLY
                    || *status == StatusCode::TOO_MANY_REQUESTS
                    || status.is_server_error()
            }
            Error::Rpc(code, _msg) => {
                // Retry internal server errors / rate-limit style custom codes.
                *code == -32000 || *code == -32001 || *code == -32002
            }
            _ => false,
        }
    }

    async fn sleep_backoff(&self, attempt: u32) {
        // attempt = 0 → base, 1 → 2x, etc., capped to 3s
        let base = self.retry_base.as_millis() as u64;
        let pow = 1u64.saturating_shl(attempt.min(6)); // cap growth
        let max_ms = (base.saturating_mul(pow)).min(3_000);
        let jitter = fastrand::u64(0..=max_ms / 2);
        let dur = Duration::from_millis(max_ms / 2 + jitter);
        tokio::time::sleep(dur).await
    }
}

// --------------------------- helpers -----------------------------------------

fn truncate_body(bytes: &[u8]) -> String {
    const LIM: usize = 512;
    let s = String::from_utf8_lossy(bytes);
    if s.len() > LIM {
        format!("{}...[+{}B]", &s[..LIM], s.len() - LIM)
    } else {
        s.into_owned()
    }
}

fn http_status_error(status: StatusCode, body: &[u8]) -> Error {
    let snippet = truncate_body(body);
    Error::Http(status, format!("http {}: {}", status.as_u16(), snippet))
}

fn contains_any(haystack: &str, needles: &[&str]) -> bool {
    let hs = haystack.to_ascii_lowercase();
    needles.iter().any(|n| hs.contains(&n.to_ascii_lowercase()))
}

// ------------------------------ tests ----------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builder_defaults() {
        let b = HttpClient::builder("http://localhost:8545").unwrap();
        let c = b.build().unwrap();
        assert_eq!(c.max_retries, 3);
    }

    #[test]
    fn id_increments() {
        let c = HttpClient::builder("http://localhost:8545").unwrap().build().unwrap();
        let a = c.next_id();
        let b = c.next_id();
        assert!(b > a);
    }

    #[test]
    fn retry_heuristics() {
        let c = HttpClient::builder("http://localhost:8545").unwrap().build().unwrap();
        assert!(c.should_retry(&Error::Transport("connection reset by peer".into())));
        assert!(c.should_retry(&Error::Http(StatusCode::INTERNAL_SERVER_ERROR, "oops".into())));
        assert!(!c.should_retry(&Error::Rpc(-32601, "method not found".into())));
    }
}
