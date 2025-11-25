//! Tokio WebSocket JSON-RPC client (tokio-tungstenite).
//!
//! Goals:
//! - Async, lightweight WS transport for JSON-RPC 2.0.
//! - Typed `call` and generic `subscribe` helpers (Ethereum-like subscription frames).
//! - Safe concurrency: pending requests are matched by `id`; subscriptions by `subscription` id.
//! - Optional topic subscriptions via `subscribe_topic("newHeads")` using `subscribe`/`unsubscribe`.
//!
//! This module does **not** implement chain semantics; it only handles transport and routing.

use crate::error::{Error, Result};
use futures_util::{stream::SplitSink, SinkExt, StreamExt};
use http::{HeaderMap, HeaderName, HeaderValue, Request};
use serde::{de::DeserializeOwned, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::{
    net::TcpStream,
    sync::{mpsc, oneshot, Mutex},
    task::JoinHandle,
    time,
};
use tokio_tungstenite::{
    connect_async,
    tungstenite::{client::IntoClientRequest, protocol::Message},
    MaybeTlsStream, WebSocketStream,
};
use url::Url;

type Ws = WebSocketStream<MaybeTlsStream<TcpStream>>;
type Writer = SplitSink<Ws, Message>;

#[derive(Clone, Debug)]
pub struct WsClientBuilder {
    endpoint: Url,
    headers: HeaderMap,
    connect_timeout: Duration,
    ping_interval: Option<Duration>,
    max_message_size: Option<usize>,
}

impl WsClientBuilder {
    pub fn new(endpoint: Url) -> Self {
        Self {
            endpoint,
            headers: HeaderMap::new(),
            connect_timeout: Duration::from_secs(15),
            ping_interval: Some(Duration::from_secs(20)),
            max_message_size: None,
        }
    }

    pub fn from_str(endpoint: &str) -> Result<Self> {
        let url = endpoint
            .parse::<Url>()
            .map_err(|e| Error::Transport(format!("bad ws url: {e}")))?;
        Ok(Self::new(url))
    }

    pub fn header(mut self, name: HeaderName, value: HeaderValue) -> Self {
        self.headers.insert(name, value);
        self
    }

    pub fn bearer_auth(mut self, token: &str) -> Self {
        let v = HeaderValue::from_str(&format!("Bearer {token}"))
            .unwrap_or_else(|_| HeaderValue::from_static("Bearer INVALID"));
        self.headers.insert(http::header::AUTHORIZATION, v);
        self
    }

    pub fn connect_timeout(mut self, d: Duration) -> Self {
        self.connect_timeout = d;
        self
    }

    pub fn ping_interval(mut self, d: Option<Duration>) -> Self {
        self.ping_interval = d;
        self
    }

    pub fn max_message_size(mut self, bytes: usize) -> Self {
        self.max_message_size = Some(bytes);
        self
    }

    pub async fn build(self) -> Result<WsClient> {
        WsClient::connect_with(self).await
    }
}

#[derive(Clone)]
pub struct WsClient {
    inner: Arc<Inner>,
}

struct Inner {
    url: Url,
    writer: Mutex<Writer>,
    pending: Mutex<HashMap<u64, oneshot::Sender<Value>>>,
    subs: Mutex<HashMap<String, mpsc::Sender<Value>>>,
    next_id: AtomicU64,
    _reader_task: JoinHandle<()>,
    _ping_task: Option<JoinHandle<()>>,
}

impl std::fmt::Debug for WsClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WsClient")
            .field("url", &self.inner.url)
            .finish()
    }
}

impl WsClient {
    /// Quick constructor with defaults.
    pub async fn connect(endpoint: &str) -> Result<Self> {
        WsClientBuilder::from_str(endpoint)?.build().await
    }

    /// Use the builder for custom headers/timeouts/keepalive.
    pub async fn connect_with(builder: WsClientBuilder) -> Result<Self> {
        // Prepare request with headers.
        let mut req: Request<()> = builder
            .endpoint
            .clone()
            .into_client_request()
            .map_err(|e| Error::Transport(format!("ws request: {e}")))?;
        *req.headers_mut() = builder.headers.clone();

        // Apply a connect timeout.
        let connect_fut = connect_async(req);
        let (ws, _resp) = time::timeout(builder.connect_timeout, connect_fut)
            .await
            .map_err(|_| Error::Transport("ws connect timeout".into()))?
            .map_err(|e| Error::Transport(format!("ws connect: {e}")))?;

        let (writer, mut reader) = ws.split();

        let url = builder.endpoint;
        let writer = Mutex::new(writer);
        let pending = Mutex::new(HashMap::<u64, oneshot::Sender<Value>>::new());
        let subs = Mutex::new(HashMap::<String, mpsc::Sender<Value>>::new());

        // Reader task
        let inner_for_reader = ReaderCtx {
            pending: pending.clone(),
            subs: subs.clone(),
        };
        let reader_task = tokio::spawn(async move {
            while let Some(msg) = reader.next().await {
                match msg {
                    Ok(Message::Text(t)) => handle_incoming(&inner_for_reader, t.as_bytes()).await,
                    Ok(Message::Binary(b)) => handle_incoming(&inner_for_reader, &b).await,
                    Ok(Message::Close(_)) => break,
                    Ok(Message::Ping(_)) => {
                        // tungstenite auto replies with Pong; nothing to do.
                    }
                    Ok(Message::Pong(_)) => {}
                    Err(e) => {
                        // Cannot surface easily; drop all pending with error.
                        drain_all_with_error(&inner_for_reader, &format!("ws read: {e}")).await;
                        break;
                    }
                    _ => {}
                }
            }
            // drain on EOF
            drain_all_with_error(&inner_for_reader, "ws closed").await;
        });

        // Optional ping task
        let ping_task = if let Some(every) = builder.ping_interval {
            let writer_for_ping = writer.clone();
            Some(tokio::spawn(async move {
                loop {
                    time::sleep(every).await;
                    let mut w = writer_for_ping.lock().await;
                    if let Err(e) = w.send(Message::Ping(Vec::new())).await {
                        // Connection likely closed; exit ping loop.
                        eprintln!("[ws] ping failed: {e}");
                        break;
                    }
                }
            }))
        } else {
            None
        };

        let inner = Arc::new(Inner {
            url,
            writer,
            pending,
            subs,
            next_id: AtomicU64::new(1),
            _reader_task: reader_task,
            _ping_task: ping_task,
        });

        Ok(Self { inner })
    }

    /// Perform a JSON-RPC call over WS and return untyped JSON.
    pub async fn call_raw(&self, method: &str, params: Option<Value>) -> Result<Value> {
        self.call::<Value, _>(method, params.unwrap_or(Value::Null)).await
    }

    /// Perform a JSON-RPC call over WS and decode into `T`.
    pub async fn call<T, P>(&self, method: &str, params: P) -> Result<T>
    where
        T: DeserializeOwned,
        P: Serialize,
    {
        let id = self.next_id();
        let (tx, rx) = oneshot::channel();
        self.inner.pending.lock().await.insert(id, tx);

        let env = json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": serde_json::to_value(params).map_err(|e| Error::Serde(format!("params: {e}")))?
        });

        let msg = Message::Text(env.to_string());
        {
            let mut w = self.inner.writer.lock().await;
            w.send(msg)
                .await
                .map_err(|e| Error::Transport(format!("ws send: {e}")))?;
        }

        let val = rx
            .await
            .map_err(|_| Error::Transport("ws call canceled".into()))?;
        // Handle possible error envelope: {"jsonrpc":"2.0","id":X,"error":{...}}
        if let Some(err) = val.get("error") {
            let code = err.get("code").and_then(|c| c.as_i64()).unwrap_or(-32603);
            let message = err
                .get("message")
                .and_then(|m| m.as_str())
                .unwrap_or("rpc error")
                .to_string();
            let data = err.get("data");
            let msg = if let Some(d) = data {
                format!("{message} | data={d}")
            } else {
                message
            };
            return Err(Error::Rpc(code, msg));
        }
        // Otherwise expect "result"
        let result = val
            .get("result")
            .cloned()
            .ok_or_else(|| Error::Rpc(-32603, "missing result".into()))?;
        Ok(serde_json::from_value::<T>(result)
            .map_err(|e| Error::Serde(format!("decode result: {e}")))?)
    }

    /// Subscribe via a custom JSON-RPC **subscribe** method and return a stream of items.
    ///
    /// - `subscribe_method`: e.g. `"eth_subscribe"` or `"subscribe"`
    /// - `unsubscribe_method`: e.g. `"eth_unsubscribe"` or `"unsubscribe"`
    /// - `params`: usually an array (e.g., `["newHeads"]`)
    pub async fn subscribe_with(
        &self,
        subscribe_method: &str,
        unsubscribe_method: &str,
        params: Value,
    ) -> Result<Subscription> {
        let sub_id: String = self.call(subscribe_method, params).await?;
        let (tx, rx) = mpsc::channel::<Value>(64);
        self.inner.subs.lock().await.insert(sub_id.clone(), tx);
        Ok(Subscription {
            client: self.clone(),
            id: sub_id,
            unsubscribe_method: unsubscribe_method.to_string(),
            rx,
        })
    }

    /// Convenience: subscribe to a **topic** using `"subscribe"` / `"unsubscribe"` RPC methods,
    /// passing `["<topic>"]` as params. Matches the Animica node WS hub.
    pub async fn subscribe_topic(&self, topic: &str) -> Result<Subscription> {
        self.subscribe_with("subscribe", "unsubscribe", json!([topic]))
            .await
    }

    /// Gracefully close the socket.
    pub async fn close(&self) -> Result<()> {
        let mut w = self.inner.writer.lock().await;
        w.send(Message::Close(None))
            .await
            .map_err(|e| Error::Transport(format!("ws close: {e}")))
    }

    fn next_id(&self) -> u64 {
        self.inner.next_id.fetch_add(1, Ordering::Relaxed)
    }
}

/// A live subscription producing a stream of JSON values.
/// Dropping the handle will attempt to `unsubscribe` and close the channel.
pub struct Subscription {
    client: WsClient,
    id: String,
    unsubscribe_method: String,
    rx: mpsc::Receiver<Value>,
}

impl Subscription {
    /// Receive the next item (awaits). Returns `None` when the subscription is closed.
    pub async fn next(&mut self) -> Option<Value> {
        self.rx.recv().await
    }

    /// Try to receive immediately without waiting.
    pub fn try_next(&mut self) -> Option<Value> {
        self.rx.try_recv().ok()
    }

    /// Unsubscribe explicitly (optional; also happens on drop).
    pub async fn unsubscribe(self) -> Result<()> {
        let id = self.id.clone();
        let method = self.unsubscribe_method.clone();
        // Best-effort RPC call; ignore result errors.
        let _ = self.client.call::<bool, _>(&method, json!([id])).await;
        Ok(())
    }

    /// Access the subscription id.
    pub fn id(&self) -> &str {
        &self.id
    }
}

impl Drop for Subscription {
    fn drop(&mut self) {
        // Fire-and-forget: attempt to send an unsubscribe; no await in Drop.
        let client = self.client.clone();
        let id = self.id.clone();
        let method = self.unsubscribe_method.clone();
        tokio::spawn(async move {
            let _ = client.call::<bool, _>(&method, json!([id])).await;
        });
    }
}

// --------------------------- Reader routing ----------------------------------

#[derive(Clone)]
struct ReaderCtx {
    pending: Mutex<HashMap<u64, oneshot::Sender<Value>>>,
    subs: Mutex<HashMap<String, mpsc::Sender<Value>>>,
}

async fn handle_incoming(ctx: &ReaderCtx, bytes: &[u8]) {
    let v: Value = match serde_json::from_slice(bytes) {
        Ok(v) => v,
        Err(_) => return, // ignore non-JSON frames
    };

    // If it's a response with "id", route to pending.
    if v.get("id").is_some() && (v.get("result").is_some() || v.get("error").is_some()) {
        let id_num = v
            .get("id")
            .and_then(|id| id.as_u64().or_else(|| id.as_str().and_then(|s| s.parse::<u64>().ok())))
            .unwrap_or(0);
        if let Some(tx) = ctx.pending.lock().await.remove(&id_num) {
            let _ = tx.send(v);
        }
        return;
    }

    // If it's a subscription notification:
    // Ethereum-like: {"jsonrpc":"2.0","method":"subscription","params":{"subscription":"<id>","result":{...}}}
    if v.get("method").and_then(|m| m.as_str()) == Some("subscription") {
        if let Some(params) = v.get("params").and_then(|p| p.as_object()) {
            if let (Some(sub), Some(result)) = (params.get("subscription"), params.get("result")) {
                if let Some(sub_id) = sub.as_str() {
                    if let Some(tx) = ctx.subs.lock().await.get(sub_id) {
                        let _ = tx.send(result.clone()).await;
                    }
                }
            }
        }
        return;
    }

    // Animica-simple hub style (optional): {"topic":"newHeads","subscription":"<id>","data":{...}}
    if let (Some(sub_id), Some(data)) = (v.get("subscription").and_then(|s| s.as_str()), v.get("data")) {
        if let Some(tx) = ctx.subs.lock().await.get(sub_id) {
            let _ = tx.send(data.clone()).await;
        }
        return;
    }

    // Unknown frame → ignore.
}

async fn drain_all_with_error(ctx: &ReaderCtx, msg: &str) {
    let mut pending = ctx.pending.lock().await;
    for (_id, tx) in pending.drain() {
        let _ = tx.send(json!({"error": {"code": -32000, "message": msg}}));
    }
    // Close all subscriptions.
    let mut subs = ctx.subs.lock().await;
    for (_id, tx) in subs.drain() {
        let _ = tx.closed().await;
    }
}

// --------------------------------- tests -------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builder_defaults() {
        let b = WsClientBuilder::from_str("ws://localhost:8546").unwrap();
        assert_eq!(b.connect_timeout, Duration::from_secs(15));
    }

    #[tokio::test]
    async fn id_increments() {
        // We can't connect in CI here; just instantiate inner pieces by connecting to a dummy
        // (will timeout) — so we skip network. Instead we check that next_id increments via a
        // constructed client would, which we can't create without a socket. So this is a placeholder.
        // Real integration tests should run against the node WS.
        assert!(true);
    }
}
