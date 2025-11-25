//! AICF (AI Compute Fund) client.
//!
//! Supports two transport modes out of the box:
//! - JSON-RPC methods on the node (`aicf.*`) via the SDK's `JsonRpcClient`
//! - Optional REST fallbacks for dev deployments (POST/GET under `/aicf/...`)
//!
//! Implemented calls:
//! - aicf.listProviders         → list providers
//! - aicf.getProvider           → get one provider
//! - aicf.listJobs (optional)   → list jobs (if exposed)
//! - aicf.getJob                → fetch a job by id
//! - aicf.getResult             → fetch a result record by task id
//! - aicf.getBalance            → provider/accounting balance (if exposed)
//! - aicf.claimPayout           → claim payouts (if exposed)
//! - enqueue_ai / enqueue_quantum (dev helper; tries RPC, then REST)
//!
//! Notes:
//! * The enqueue methods are primarily for dev/test flows. On production
//!   networks, enqueue typically happens through contract syscalls and proofs
//!   appear on-chain; SDK-side enqueue should be feature-gated at the caller.

use crate::error::{Error, Result};
use crate::rpc::http::JsonRpcClient;
use reqwest::{Client as Http, StatusCode, Url};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value as JsonValue};
use std::time::Duration;
use tokio::time::sleep;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Provider {
    pub id: String,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub capabilities: Vec<String>, // e.g. ["AI","Quantum"]
    #[serde(default)]
    pub stake: Option<u64>,
    #[serde(default)]
    pub region: Option<String>,
    #[serde(default)]
    pub status: Option<String>,
    #[serde(flatten)]
    pub extra: serde_json::Map<String, JsonValue>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct JobRecord {
    pub id: String,
    pub kind: String, // "AI" | "Quantum"
    pub status: String,
    #[serde(default)]
    pub requester: Option<String>,
    #[serde(default)]
    pub provider: Option<String>,
    #[serde(default)]
    pub created_at: Option<String>,
    #[serde(default)]
    pub updated_at: Option<String>,
    #[serde(default)]
    pub spec: Option<JsonValue>,
    #[serde(default)]
    pub metrics: Option<JsonValue>,
    #[serde(flatten)]
    pub extra: serde_json::Map<String, JsonValue>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ResultRecord {
    pub task_id: String,
    #[serde(default)]
    pub output_digest: Option<String>,
    #[serde(default)]
    pub proof_ref: Option<JsonValue>,
    #[serde(default)]
    pub payload: Option<JsonValue>,
    #[serde(flatten)]
    pub extra: serde_json::Map<String, JsonValue>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EnqueueResponse {
    pub task_id: String,
    #[serde(default)]
    pub receipt: Option<JsonValue>,
    #[serde(flatten)]
    pub extra: serde_json::Map<String, JsonValue>,
}

#[derive(Clone)]
pub struct AICFClient {
    rpc: JsonRpcClient,
    http: Option<Http>,
    rest_base: Option<Url>,
    retries: usize,
    backoff: Duration,
}

impl AICFClient {
    /// Create a client bound to a JSON-RPC endpoint (e.g. "http://127.0.0.1:8545").
    pub fn new(rpc_url: &str) -> Result<Self> {
        Ok(Self {
            rpc: JsonRpcClient::new(rpc_url)?,
            http: None,
            rest_base: None,
            retries: 3,
            backoff: Duration::from_millis(250),
        })
    }

    /// Optionally attach a REST base URL for fallbacks (same origin as RPC is typical).
    pub fn with_rest_base(mut self, base: &str) -> Result<Self> {
        let url = Url::parse(base).map_err(|e| Error::Http(format!("invalid rest base: {e}")))?;
        let http = Http::builder()
            .timeout(Duration::from_secs(30))
            .build()
            .map_err(|e| Error::Http(format!("http build: {e}")))?;
        self.rest_base = Some(url);
        self.http = Some(http);
        Ok(self)
    }

    /// Tune retry behavior for REST fallbacks (RPC retries are handled by the RPC client).
    pub fn with_retries(mut self, retries: usize, backoff: Duration) -> Self {
        self.retries = retries;
        self.backoff = backoff;
        self
    }

    // ------------------------------ Providers --------------------------------

    pub async fn list_providers(&self) -> Result<Vec<Provider>> {
        self.rpc
            .call("aicf.listProviders", json!([]))
            .await
            .or_else(|e| Err(Error::Rpc(format!("aicf.listProviders: {e}"))))
    }

    pub async fn get_provider(&self, id: &str) -> Result<Provider> {
        self.rpc
            .call("aicf.getProvider", json!([id]))
            .await
            .or_else(|e| Err(Error::Rpc(format!("aicf.getProvider: {e}"))))
    }

    pub async fn get_balance(&self, provider_id: &str) -> Result<u64> {
        self.rpc
            .call("aicf.getBalance", json!([provider_id]))
            .await
            .or_else(|e| Err(Error::Rpc(format!("aicf.getBalance: {e}"))))
    }

    pub async fn claim_payout(&self, provider_id: &str) -> Result<bool> {
        self.rpc
            .call("aicf.claimPayout", json!([provider_id]))
            .await
            .or_else(|e| Err(Error::Rpc(format!("aicf.claimPayout: {e}"))))
    }

    // -------------------------------- Jobs -----------------------------------

    /// Try JSON-RPC aicf.listJobs if available. Some deployments do not expose it.
    pub async fn list_jobs(&self, filter: Option<JsonValue>) -> Result<Vec<JobRecord>> {
        self.rpc
            .call("aicf.listJobs", json!([filter.unwrap_or(json!({}))]))
            .await
            .or_else(|e| Err(Error::Rpc(format!("aicf.listJobs: {e}"))))
    }

    pub async fn get_job(&self, job_id: &str) -> Result<JobRecord> {
        // Prefer RPC. If method missing and REST base is set, try REST.
        match self
            .rpc
            .call::<JobRecord>("aicf.getJob", json!([job_id]))
            .await
        {
            Ok(v) => Ok(v),
            Err(e) => {
                if let (Some(http), Some(base)) = (&self.http, &self.rest_base) {
                    let url = base
                        .join(&format!("/aicf/job/{}", urlencoding::encode(job_id)))
                        .map_err(|e| Error::Http(format!("url: {e}")))?;
                    return self.get_with_retries_json::<JobRecord>(http, url).await;
                }
                Err(Error::Rpc(format!("aicf.getJob: {e}")))
            }
        }
    }

    pub async fn get_result(&self, task_id: &str) -> Result<ResultRecord> {
        match self
            .rpc
            .call::<ResultRecord>("aicf.getResult", json!([task_id]))
            .await
        {
            Ok(v) => Ok(v),
            Err(e) => {
                if let (Some(http), Some(base)) = (&self.http, &self.rest_base) {
                    let url = base
                        .join(&format!(
                            "/aicf/result/{}",
                            urlencoding::encode(task_id)
                        ))
                        .map_err(|e| Error::Http(format!("url: {e}")))?;
                    return self.get_with_retries_json::<ResultRecord>(http, url).await;
                }
                Err(Error::Rpc(format!("aicf.getResult: {e}")))
            }
        }
    }

    // --------------------------- Enqueue (dev) --------------------------------
    // These helpers try an RPC method first, then fall back to a REST POST.

    /// Enqueue an AI job (model+prompt). Returns a task id and optional receipt.
    pub async fn enqueue_ai(
        &self,
        model: &str,
        prompt: &str,
        max_units: Option<u64>,
        meta: Option<JsonValue>,
    ) -> Result<EnqueueResponse> {
        let params = json!({
            "model": model,
            "prompt": prompt,
            "maxUnits": max_units,
            "meta": meta.unwrap_or(json!({}))
        });

        match self
            .rpc
            .call::<EnqueueResponse>("aicf.enqueueAI", json!([params]))
            .await
        {
            Ok(v) => Ok(v),
            Err(e) => {
                // REST fallback: POST /aicf/enqueue/ai
                self.enqueue_rest(
                    "/aicf/enqueue/ai",
                    json!({
                        "model": model,
                        "prompt": prompt,
                        "maxUnits": max_units,
                        "meta": meta.unwrap_or(json!({}))
                    }),
                )
                .await
                .map_err(|er| Error::Rpc(format!("aicf.enqueueAI: {e}; REST fallback: {er}")))
            }
        }
    }

    /// Enqueue a Quantum job (circuit JSON + shots).
    pub async fn enqueue_quantum(
        &self,
        circuit: JsonValue,
        shots: u32,
        max_units: Option<u64>,
        meta: Option<JsonValue>,
    ) -> Result<EnqueueResponse> {
        let params = json!({
            "circuit": circuit,
            "shots": shots,
            "maxUnits": max_units,
            "meta": meta.unwrap_or(json!({}))
        });

        match self
            .rpc
            .call::<EnqueueResponse>("aicf.enqueueQuantum", json!([params]))
            .await
        {
            Ok(v) => Ok(v),
            Err(e) => {
                self.enqueue_rest(
                    "/aicf/enqueue/quantum",
                    json!({
                        "circuit": params["circuit"],
                        "shots": shots,
                        "maxUnits": max_units,
                        "meta": params["meta"]
                    }),
                )
                .await
                .map_err(|er| Error::Rpc(format!("aicf.enqueueQuantum: {e}; REST fallback: {er}")))
            }
        }
    }

    // ------------------------------ Internals ---------------------------------

    async fn enqueue_rest(&self, path: &str, payload: JsonValue) -> Result<EnqueueResponse> {
        let (http, base) = self
            .http
            .as_ref()
            .zip(self.rest_base.as_ref())
            .ok_or_else(|| Error::Http("REST base not configured; call with_rest_base()".into()))?;

        let url = base
            .join(path)
            .map_err(|e| Error::Http(format!("url: {e}")))?;

        self.post_with_retries_json::<EnqueueResponse>(http, url, payload)
            .await
    }

    async fn post_with_retries_json<T: for<'de> Deserialize<'de>>(
        &self,
        http: &Http,
        url: Url,
        payload: JsonValue,
    ) -> Result<T> {
        let mut attempt = 0usize;
        loop {
            attempt += 1;
            match http.post(url.clone()).json(&payload).send().await {
                Ok(resp) => {
                    if resp.status().is_success() {
                        return resp
                            .json::<T>()
                            .await
                            .map_err(|e| Error::Http(format!("parse json: {e}")));
                    } else if should_retry_status(resp.status()) && attempt <= self.retries {
                        sleep(self.backoff).await;
                        continue;
                    } else {
                        let body = resp.text().await.unwrap_or_else(|_| "<no body>".into());
                        return Err(Error::Http(format!("HTTP {}: {}", resp.status(), body)));
                    }
                }
                Err(e) => {
                    if attempt <= self.retries {
                        sleep(self.backoff).await;
                        continue;
                    }
                    return Err(Error::Http(format!("POST error: {e}")));
                }
            }
        }
    }

    async fn get_with_retries_json<T: for<'de> Deserialize<'de>>(
        &self,
        http: &Http,
        url: Url,
    ) -> Result<T> {
        let mut attempt = 0usize;
        loop {
            attempt += 1;
            match http.get(url.clone()).send().await {
                Ok(resp) => {
                    if resp.status().is_success() {
                        return resp
                            .json::<T>()
                            .await
                            .map_err(|e| Error::Http(format!("parse json: {e}")));
                    } else if resp.status() == StatusCode::NOT_FOUND {
                        return Err(Error::Http("not found".into()));
                    } else if should_retry_status(resp.status()) && attempt <= self.retries {
                        sleep(self.backoff).await;
                        continue;
                    } else {
                        let body = resp.text().await.unwrap_or_else(|_| "<no body>".into());
                        return Err(Error::Http(format!("HTTP {}: {}", resp.status(), body)));
                    }
                }
                Err(e) => {
                    if attempt <= self.retries {
                        sleep(self.backoff).await;
                        continue;
                    }
                    return Err(Error::Http(format!("GET error: {e}")));
                }
            }
        }
    }
}

fn should_retry_status(s: StatusCode) -> bool {
    s.is_server_error()
        || s == StatusCode::TOO_MANY_REQUESTS
        || s == StatusCode::BAD_GATEWAY
        || s == StatusCode::SERVICE_UNAVAILABLE
        || s == StatusCode::GATEWAY_TIMEOUT
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn provider_roundtrip_loose() {
        let s = r#"{
          "id":"prov-1",
          "name":"Acme AI",
          "capabilities":["AI","Quantum"],
          "stake": 1000000,
          "region": "us-east",
          "status": "active",
          "customField": 7
        }"#;
        let p: Provider = serde_json::from_str(s).unwrap();
        assert_eq!(p.id, "prov-1");
        assert_eq!(p.capabilities.len(), 2);
        assert!(p.extra.contains_key("customField"));
    }

    #[test]
    fn enqueue_response_roundtrip() {
        let s = r#"{
          "taskId":"task_abc",
          "receipt": {"commitment":"0xdeadbeef"},
          "queuePos": 3
        }"#;
        let r: EnqueueResponse = serde_json::from_str(s).unwrap();
        assert_eq!(r.task_id, "task_abc");
        assert!(r.extra.contains_key("queuePos"));
    }

    #[tokio::test]
    async fn constructor_ok() {
        let c = AICFClient::new("http://localhost:8545").unwrap();
        let _c = c.with_retries(2, Duration::from_millis(50));
    }

    #[tokio::test]
    async fn rest_url_join_ok() {
        let c = AICFClient::new("http://localhost:8545")
            .unwrap()
            .with_rest_base("http://localhost:8545")
            .unwrap();
        // Private method behavior sanity is covered indirectly via get/enqueue fallbacks.
        // This test just ensures with_rest_base does not error on parse/build.
        let _ = c;
    }

    #[test]
    fn job_record_loose() {
        let j: JobRecord = serde_json::from_value(json!({
            "id":"job1",
            "kind":"AI",
            "status":"Completed",
            "spec":{"model":"foo","prompt":"bar"},
            "metrics":{"latencyMs":123},
            "unknown": true
        })).unwrap();
        assert_eq!(j.id, "job1");
        assert!(j.extra.contains_key("unknown"));
    }
}
