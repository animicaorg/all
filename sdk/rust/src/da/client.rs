//! Data Availability (DA) client — post/get/proof helpers.
//!
//! This talks to the DA REST endpoints mounted by the node (FastAPI):
//! - `POST /da/blob?ns=<u32>`    — submit raw blob bytes; returns commitment/receipt
//! - `GET  /da/blob/{commitment}` — fetch raw blob bytes (exact payload)
//! - `GET  /da/blob/{commitment}/proof` — fetch light-client proof JSON
//!
//! These endpoints are mounted alongside the JSON-RPC service, so you can point
//! this client at the same base URL (e.g. `http://127.0.0.1:8545`).
//!
//! The response shapes are intentionally loose/future-proof (serde_json::Value)
//! except for the common commitment/namespace/size receipt fields.

use crate::error::{Error, Result};
use reqwest::{Client, StatusCode, Url};
use serde::{Deserialize, Serialize};
use serde_json::Value as JsonValue;
use std::time::Duration;
use tokio::time::sleep;

/// Result of a DA blob POST. Mirrors the common fields exposed by the service.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DaPutResult {
    /// Commitment / NMT root (0x-hex).
    pub commitment: String,
    /// Namespace id (u32).
    pub namespace: u32,
    /// Total original blob size in bytes.
    pub size: u64,
    /// Any extra fields the server returns (kept for forward-compat).
    #[serde(flatten)]
    pub extra: serde_json::Map<String, JsonValue>,
}

/// Data Availability REST client.
#[derive(Clone)]
pub struct DAClient {
    base: Url,
    http: Client,
    timeout: Duration,
    retries: usize,
    backoff: Duration,
}

impl DAClient {
    /// Create a new client from a base URL (e.g. "http://127.0.0.1:8545").
    pub fn new(base_url: &str) -> Result<Self> {
        let base = Url::parse(base_url)
            .map_err(|e| Error::Http(format!("invalid base URL: {e}")))?;
        let http = Client::builder()
            .timeout(Duration::from_secs(30))
            .build()
            .map_err(|e| Error::Http(format!("http client build: {e}")))?;
        Ok(Self {
            base,
            http,
            timeout: Duration::from_secs(30),
            retries: 3,
            backoff: Duration::from_millis(250),
        })
    }

    /// Adjust request timeout (default 30s).
    pub fn with_timeout(mut self, t: Duration) -> Self {
        self.timeout = t;
        self
    }

    /// Adjust retry attempts for transient failures (default 3).
    pub fn with_retries(mut self, n: usize) -> Self {
        self.retries = n;
        self
    }

    /// Adjust backoff between retries (default 250ms).
    pub fn with_backoff(mut self, d: Duration) -> Self {
        self.backoff = d;
        self
    }

    fn url(&self, path: &str) -> Result<Url> {
        self.base
            .join(path)
            .map_err(|e| Error::Http(format!("url join: {e}")))
    }

    /// POST a blob under a namespace. Returns the server's receipt containing the
    /// `commitment` (hex), `namespace`, and `size`. The request sends the raw
    /// bytes with `Content-Type: application/octet-stream` and a `ns` query param.
    pub async fn post_blob<B>(&self, namespace: u32, data: B) -> Result<DaPutResult>
    where
        B: AsRef<[u8]>,
    {
        let mut url = self.url("/da/blob")?;
        {
            let mut qp = url.query_pairs_mut();
            qp.append_pair("ns", &namespace.to_string());
        }

        // Try octet-stream; if server responds 415, fall back to JSON envelope.
        let body = data.as_ref().to_vec();

        // Attempt 1: octet-stream
        match self
            .with_retries_post_octet(url.clone(), body.clone())
            .await
        {
            Ok(v) => return Ok(v),
            Err(Error::Http(ref s)) if s.contains("415") => {
                // fall through to JSON path
            }
            Err(e) => return Err(e),
        }

        // Attempt 2: JSON envelope (some deployments may prefer this)
        let payload = serde_json::json!({
            "namespace": namespace,
            "data": format!("0x{}", hex::encode(&body)),
        });
        self.with_retries_post_json(url, payload).await
    }

    /// GET raw blob bytes by `commitment` (0x-hex).
    pub async fn get_blob(&self, commitment: &str) -> Result<Vec<u8>> {
        let safe = percent_encode(commitment);
        let url = self.url(&format!("/da/blob/{safe}"))?;
        self.with_retries_get_bytes(url).await
    }

    /// GET availability proof JSON for a blob commitment.
    pub async fn get_proof(&self, commitment: &str) -> Result<JsonValue> {
        let safe = percent_encode(commitment);
        let url = self.url(&format!("/da/blob/{safe}/proof"))?;
        self.with_retries_get_json(url).await
    }

    // --------------------------- Retry wrappers ------------------------------

    async fn with_retries_post_octet(&self, url: Url, body: Vec<u8>) -> Result<DaPutResult> {
        let mut attempt = 0usize;
        loop {
            attempt += 1;
            let resp = self
                .http
                .post(url.clone())
                .header("Content-Type", "application/octet-stream")
                .body(body.clone())
                .send()
                .await;

            match resp {
                Ok(r) => {
                    if r.status() == StatusCode::UNSUPPORTED_MEDIA_TYPE {
                        return Err(Error::Http("415 unsupported media type".into()));
                    }
                    if r.status().is_success() {
                        let json = r.json::<serde_json::Map<String, JsonValue>>().await
                            .map_err(|e| Error::Http(format!("parse DA POST json: {e}")))?;
                        let commitment = json.get("commitment")
                            .and_then(|v| v.as_str())
                            .ok_or_else(|| Error::Http("missing commitment in response".into()))?
                            .to_string();
                        let namespace = json.get("namespace")
                            .and_then(|v| v.as_u64())
                            .ok_or_else(|| Error::Http("missing namespace in response".into()))? as u32;
                        let size = json.get("size")
                            .and_then(|v| v.as_u64())
                            .ok_or_else(|| Error::Http("missing size in response".into()))?;
                        let extra = json.into_iter().filter(|(k,_)| k != "commitment" && k != "namespace" && k != "size")
                            .collect();
                        return Ok(DaPutResult { commitment, namespace, size, extra });
                    } else if should_retry_status(r.status()) && attempt <= self.retries {
                        sleep(self.backoff).await;
                        continue;
                    } else {
                        return Err(Error::Http(format!(
                            "DA POST failed: {}",
                            r.text().await.unwrap_or_else(|_| "<no body>".into())
                        )));
                    }
                }
                Err(e) => {
                    if attempt <= self.retries {
                        sleep(self.backoff).await;
                        continue;
                    }
                    return Err(Error::Http(format!("DA POST error: {e}")));
                }
            }
        }
    }

    async fn with_retries_post_json(&self, url: Url, payload: JsonValue) -> Result<DaPutResult> {
        let mut attempt = 0usize;
        loop {
            attempt += 1;
            let resp = self.http.post(url.clone()).json(&payload).send().await;

            match resp {
                Ok(r) => {
                    if r.status().is_success() {
                        let json = r.json::<serde_json::Map<String, JsonValue>>().await
                            .map_err(|e| Error::Http(format!("parse DA POST json: {e}")))?;
                        let commitment = json.get("commitment")
                            .and_then(|v| v.as_str())
                            .ok_or_else(|| Error::Http("missing commitment in response".into()))?
                            .to_string();
                        let namespace = json.get("namespace")
                            .and_then(|v| v.as_u64())
                            .ok_or_else(|| Error::Http("missing namespace in response".into()))? as u32;
                        let size = json.get("size")
                            .and_then(|v| v.as_u64())
                            .ok_or_else(|| Error::Http("missing size in response".into()))?;
                        let extra = json.into_iter().filter(|(k,_)| k != "commitment" && k != "namespace" && k != "size")
                            .collect();
                        return Ok(DaPutResult { commitment, namespace, size, extra });
                    } else if should_retry_status(r.status()) && attempt <= self.retries {
                        sleep(self.backoff).await;
                        continue;
                    } else {
                        return Err(Error::Http(format!(
                            "DA POST(json) failed: {}",
                            r.text().await.unwrap_or_else(|_| "<no body>".into())
                        )));
                    }
                }
                Err(e) => {
                    if attempt <= self.retries {
                        sleep(self.backoff).await;
                        continue;
                    }
                    return Err(Error::Http(format!("DA POST(json) error: {e}")));
                }
            }
        }
    }

    async fn with_retries_get_bytes(&self, url: Url) -> Result<Vec<u8>> {
        let mut attempt = 0usize;
        loop {
            attempt += 1;
            let resp = self.http.get(url.clone()).send().await;
            match resp {
                Ok(r) => {
                    if r.status().is_success() {
                        return r.bytes().await
                            .map(|b| b.to_vec())
                            .map_err(|e| Error::Http(format!("DA GET bytes read: {e}")));
                    } else if should_retry_status(r.status()) && attempt <= self.retries {
                        sleep(self.backoff).await;
                        continue;
                    } else if r.status() == StatusCode::NOT_FOUND {
                        return Err(Error::Http("blob not found".into()));
                    } else {
                        return Err(Error::Http(format!(
                            "DA GET failed: {}",
                            r.text().await.unwrap_or_else(|_| "<no body>".into())
                        )));
                    }
                }
                Err(e) => {
                    if attempt <= self.retries {
                        sleep(self.backoff).await;
                        continue;
                    }
                    return Err(Error::Http(format!("DA GET error: {e}")));
                }
            }
        }
    }

    async fn with_retries_get_json(&self, url: Url) -> Result<JsonValue> {
        let mut attempt = 0usize;
        loop {
            attempt += 1;
            let resp = self.http.get(url.clone()).send().await;
            match resp {
                Ok(r) => {
                    if r.status().is_success() {
                        return r.json::<JsonValue>().await
                            .map_err(|e| Error::Http(format!("DA GET json parse: {e}")));
                    } else if should_retry_status(r.status()) && attempt <= self.retries {
                        sleep(self.backoff).await;
                        continue;
                    } else if r.status() == StatusCode::NOT_FOUND {
                        return Err(Error::Http("proof not found".into()));
                    } else {
                        return Err(Error::Http(format!(
                            "DA GET json failed: {}",
                            r.text().await.unwrap_or_else(|_| "<no body>".into())
                        )));
                    }
                }
                Err(e) => {
                    if attempt <= self.retries {
                        sleep(self.backoff).await;
                        continue;
                    }
                    return Err(Error::Http(format!("DA GET json error: {e}")));
                }
            }
        }
    }
}

fn should_retry_status(s: StatusCode) -> bool {
    s.is_server_error() || s == StatusCode::TOO_MANY_REQUESTS || s == StatusCode::BAD_GATEWAY || s == StatusCode::SERVICE_UNAVAILABLE || s == StatusCode::GATEWAY_TIMEOUT
}

fn percent_encode(s: &str) -> String {
    // Leave 0-9a-zA-Z and a few safe symbols, encode the rest.
    urlencoding::encode(s).into_owned()
}

// ---------------------------------- Tests ------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_urls() {
        let c = DAClient::new("http://localhost:8545").unwrap();
        let u1 = c.url("/da/blob").unwrap();
        assert_eq!(u1.as_str(), "http://localhost:8545/da/blob");

        let safe = percent_encode("0xabc123");
        let u2 = c.url(&format!("/da/blob/{safe}")).unwrap();
        assert_eq!(u2.as_str(), "http://localhost:8545/da/blob/0xabc123");
    }

    #[test]
    fn put_result_roundtrip() {
        let json = r#"{
            "commitment":"0xdeadbeef",
            "namespace": 42,
            "size": 4096,
            "nmt_root": "0x01"
        }"#;
        let v: DaPutResult = serde_json::from_str(json).unwrap();
        assert_eq!(v.commitment, "0xdeadbeef");
        assert_eq!(v.namespace, 42);
        assert_eq!(v.size, 4096);
        assert!(v.extra.contains_key("nmt_root"));
    }
}
