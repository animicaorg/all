"""YouTube Data API v3 client for the 24/7 livestream.

Handles the full lifecycle: OAuth access-token refresh, creating a reusable ingest
stream + a persistent broadcast (auto-start on data, no auto-stop = 24/7), binding
them, reading + posting live-chat messages, ending the broadcast, and resumable VOD
upload for the 1-hour segments. Tokens come from the console's localhost-only internal
state route (the only path that unseals them); the Google OAuth client id/secret come
from env (or, localhost-gated, from that same state payload).
"""
from __future__ import annotations

import json as _json
import os
import tempfile
import time

import requests

from .contract import ChatMessage

API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class YouTubeLive:
    def __init__(self, access_token: str, refresh_token: str,
                 client_id: str, client_secret: str, log=print):
        self.access = access_token
        self.refresh = refresh_token
        self.cid = client_id
        self.csecret = client_secret
        self.log = log
        self.broadcast_id = None
        self.stream_id = None
        self.live_chat_id = None
        self._chat_token = None
        self._chat_primed = False
        self._chat_next = 0.0
        # Live-chat reads are the dominant Data-API cost of a 24/7 stream (the default 10k
        # units/day is small). Poll no faster than this floor, and back off hard on quota.
        self._chat_poll_floor = float(os.environ.get("ANIMICA_STREAM_CHAT_POLL_SECS", "15"))
        # Daily read BUDGET, spread across the whole quota day (resets ~midnight Pacific).
        # A flat floor front-loads the 10k/day quota and chat goes dark by mid-day; instead
        # pace reads so the budget lasts until the next reset. Empirically a flat 15s floor
        # (~5,760 reads/day) drains the quota in well under a day, so default to ~2,400 —
        # leaving headroom for chat replies (insert) and broadcast/VOD ops out of the 10k.
        self._chat_daily_reads = float(os.environ.get("ANIMICA_STREAM_CHAT_DAILY_READS", "2400"))
        self._chat_reads_today = 0.0
        self._chat_quota_day = -1
        self._sess = requests.Session()

    @staticmethod
    def _quota_day(now: float) -> int:
        """Integer index of the current YouTube-quota day. Quota resets at midnight
        Pacific; approximate as 07:00 UTC (PDT). Used only to reset the read counter."""
        return int((now - 7 * 3600) // 86400)

    def _seconds_until_quota_reset(self, now: float) -> float:
        reset_at = (self._quota_day(now) + 1) * 86400 + 7 * 3600
        return max(60.0, reset_at - now)

    def _paced_chat_interval(self, now: float, yt_polling_secs: float) -> float:
        """Seconds until the next chat poll: never faster than YouTube's requested
        pollingInterval or our floor, and never faster than the daily read budget allows
        (spread evenly over the time remaining until the quota resets)."""
        day = self._quota_day(now)
        if day != self._chat_quota_day:      # new quota day → fresh budget
            self._chat_quota_day = day
            self._chat_reads_today = 0.0
        reads_left = self._chat_daily_reads - self._chat_reads_today
        if reads_left <= 0:                  # budget spent — idle until reset
            return self._seconds_until_quota_reset(now)
        # Spread the remaining reads evenly over the time left until reset. Never poll
        # faster than our floor or than YouTube's requested pollingInterval (honoring the
        # latter is required); adaptive naturally dominates once the even pace exceeds them.
        adaptive = self._seconds_until_quota_reset(now) / reads_left
        return max(self._chat_poll_floor, yt_polling_secs, adaptive)

    # ── construction ─────────────────────────────────────────────────────────
    @classmethod
    def from_console(cls, mkt_url: str = "", token: str = "", log=print):
        mkt_url = mkt_url or os.environ.get("ANIMICA_MKT_URL", "http://127.0.0.1:4950")
        token = token or os.environ.get("ANIMAL_INTERNAL_TOKEN", "")
        if not token:
            log("[youtube] ANIMAL_INTERNAL_TOKEN not set — cannot read the connected account")
            return None
        try:
            r = requests.get(mkt_url.rstrip("/") + "/api/mkt/v1/animal/internal/state",
                             headers={"authorization": f"Bearer {token}"}, timeout=8)
            d = r.json()
        except Exception as e:
            log(f"[youtube] console unreachable: {e}")
            return None
        yt = next((c for c in d.get("channels", []) if c.get("platform") == "youtube"), None)
        access = (yt or {}).get("accessToken") or (yt or {}).get("access_token") or ""
        refresh = (yt or {}).get("refreshToken") or (yt or {}).get("refresh_token") or ""
        creds = d.get("youtube") or {}
        cid = os.environ.get("YOUTUBE_CLIENT_ID", "") or creds.get("clientId", "")
        csec = os.environ.get("YOUTUBE_CLIENT_SECRET", "") or creds.get("clientSecret", "")
        if not access:
            log("[youtube] YouTube isn't connected in the console (open animica.dev/animal)")
            return None
        if not (refresh and cid and csec):
            log("[youtube] WARNING: no refresh token / client creds — token will expire in ~1h "
                "(set YOUTUBE_CLIENT_ID/SECRET and reconnect for 24/7)")
        return cls(access, refresh, cid, csec, log=log)

    # ── auth + request plumbing ──────────────────────────────────────────────
    def _refresh_token(self) -> bool:
        if not (self.refresh and self.cid and self.csecret):
            return False
        try:
            r = self._sess.post(TOKEN_URL, timeout=15, data={
                "client_id": self.cid, "client_secret": self.csecret,
                "refresh_token": self.refresh, "grant_type": "refresh_token"})
            j = r.json()
            if r.ok and j.get("access_token"):
                self.access = j["access_token"]
                self.log("[youtube] refreshed access token")
                return True
            self.log(f"[youtube] refresh failed: {r.status_code} {str(j)[:160]}")
        except Exception as e:
            self.log(f"[youtube] refresh error: {e}")
        return False

    def _api(self, method: str, path: str, payload=None):
        url = API + path if path.startswith("/") else path
        body = _json.dumps(payload).encode() if payload is not None else None
        for attempt in (0, 1):
            headers = {"authorization": f"Bearer {self.access}"}
            if body is not None:
                headers["content-type"] = "application/json"
            r = self._sess.request(method, url, headers=headers, data=body, timeout=30)
            if r.status_code == 401 and attempt == 0 and self._refresh_token():
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"YT {method} {path.split('?')[0]} -> {r.status_code}: {r.text[:300]}")
            return r.json() if r.content else {}
        return {}

    def verify(self) -> dict:
        """Confirm the token works and return the channel (raises on failure)."""
        d = self._api("GET", "/channels?part=snippet&mine=true")
        items = d.get("items", [])
        return items[0] if items else {}

    # ── go live ──────────────────────────────────────────────────────────────
    # Lifecycle states in which a persisted broadcast can still carry a stream. Once a
    # broadcast is "complete"/"revoked" it can never be reused, so we mint a fresh one.
    _REUSABLE = ("created", "ready", "testStarting", "testing", "liveStarting", "live")

    def ensure_live(self, title: str, description: str = "", privacy: str = "public",
                    record_dir: str = "", state_path: str = "") -> dict:
        """Reuse a persisted broadcast + reusable ingest stream when one is still usable so
        the public watch URL stays stable across service restarts / reboots. Falls back to
        creating a fresh broadcast on ANY problem, so this is never worse than go_live()."""
        prev_bid = None
        if state_path and os.path.exists(state_path):
            try:
                st = _json.load(open(state_path))
                prev_bid = st.get("broadcast_id")
                bid, sid = prev_bid, st.get("stream_id")
                if bid and sid:
                    info = self._try_reuse(bid, sid, record_dir)
                    if info:
                        self.log(f"[youtube] reusing broadcast {bid} — stable watch URL")
                        return info
                    self.log(f"[youtube] persisted broadcast {bid} no longer usable — creating a fresh one")
            except Exception as e:
                self.log(f"[youtube] reuse check failed ({e}); creating a fresh broadcast")
        # We're about to mint a NEW broadcast. First best-effort END the previous one, or it
        # would linger forever as a zombie "live" stream (enableAutoStop=False + end() gated
        # off never completes it) and _persist_state below erases its id from disk.
        if prev_bid:
            self._end_broadcast_best_effort(prev_bid)
        info = self.go_live(title, description, privacy, record_dir)
        self._persist_state(state_path, info["broadcast_id"])
        return info

    def _end_broadcast_best_effort(self, bid: str):
        try:
            self._api("POST", f"/liveBroadcasts/transition?broadcastStatus=complete&id={bid}&part=status")
            self.log(f"[youtube] ended stale broadcast {bid} before creating a fresh one")
        except Exception:
            pass  # already complete / gone — nothing to clean up

    def _try_reuse(self, bid: str, sid: str, record_dir: str):
        b = self._api("GET", f"/liveBroadcasts?part=status,snippet,contentDetails&id={bid}")
        items = b.get("items", [])
        if not items or items[0]["status"]["lifeCycleStatus"] not in self._REUSABLE:
            return None
        it = items[0]
        s = self._api("GET", f"/liveStreams?part=cdn&id={sid}")
        sit = s.get("items", [])
        if not sit:
            return None
        ing = sit[0]["cdn"]["ingestionInfo"]
        rtmp = ing["ingestionAddress"].rstrip("/") + "/" + ing["streamName"]
        self.broadcast_id, self.stream_id = bid, sid
        self.live_chat_id = it.get("snippet", {}).get("liveChatId")
        # (Re)bind only while the broadcast is still pre-live; a live broadcast is already bound.
        bound = it.get("contentDetails", {}).get("boundStreamId")
        if bound != sid and it["status"]["lifeCycleStatus"] in ("created", "ready"):
            try:
                self._api("POST", f"/liveBroadcasts/bind?id={bid}&streamId={sid}&part=id,contentDetails")
            except Exception:
                return None
        rec = record_dir or os.path.join(tempfile.gettempdir(), f"anm-vod-{bid}")
        os.makedirs(rec, exist_ok=True)
        return {"rtmp_url": rtmp, "record_dir": rec, "broadcast_id": bid,
                "live_chat_id": self.live_chat_id,
                "watch_url": f"https://www.youtube.com/watch?v={bid}"}

    def _persist_state(self, state_path: str, broadcast_id: str):
        if not state_path:
            return
        try:
            d = os.path.dirname(state_path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(state_path, "w") as f:
                _json.dump({"broadcast_id": broadcast_id, "stream_id": self.stream_id}, f)
        except Exception as e:
            self.log(f"[youtube] could not persist broadcast state: {e}")

    def go_live(self, title: str, description: str = "", privacy: str = "public",
                record_dir: str = "") -> dict:
        title = title[:100]
        s = self._api("POST", "/liveStreams?part=snippet,cdn,contentDetails", {
            "snippet": {"title": title},
            "cdn": {"ingestionType": "rtmp", "resolution": "variable", "frameRate": "variable"},
            "contentDetails": {"isReusable": True},
        })
        self.stream_id = s["id"]
        ing = s["cdn"]["ingestionInfo"]
        rtmp = ing["ingestionAddress"].rstrip("/") + "/" + ing["streamName"]

        start = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 15))
        b = self._api("POST", "/liveBroadcasts?part=snippet,status,contentDetails", {
            "snippet": {"title": title, "scheduledStartTime": start,
                        "description": description or "24/7 interactive AI livestream on the Animica network."},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
            # AI/synthetic content is disclosed here so YouTube labels it honestly.
            "contentDetails": {"enableAutoStart": True, "enableAutoStop": False,
                               "latencyPreference": "low", "enableDvr": True,
                               "monitorStream": {"enableMonitorStream": False}},
        })
        self.broadcast_id = b["id"]
        self.live_chat_id = b.get("snippet", {}).get("liveChatId")
        self._api("POST", f"/liveBroadcasts/bind?id={self.broadcast_id}"
                          f"&streamId={self.stream_id}&part=id,contentDetails")
        rec = record_dir or os.path.join(tempfile.gettempdir(), f"anm-vod-{self.broadcast_id}")
        os.makedirs(rec, exist_ok=True)
        return {"rtmp_url": rtmp, "record_dir": rec, "broadcast_id": self.broadcast_id,
                "live_chat_id": self.live_chat_id,
                "watch_url": f"https://www.youtube.com/watch?v={self.broadcast_id}"}

    def viewers(self) -> int:
        """Current concurrent viewers (0 if unavailable / not yet counted by YouTube)."""
        if not self.broadcast_id:
            return 0
        try:
            d = self._api("GET", f"/videos?part=liveStreamingDetails&id={self.broadcast_id}")
            items = d.get("items", [])
            if items:
                cv = items[0].get("liveStreamingDetails", {}).get("concurrentViewers")
                return int(cv) if cv is not None else 0
        except Exception:
            pass
        return 0

    def end(self):
        if not self.broadcast_id:
            return
        try:
            self._api("POST", f"/liveBroadcasts/transition?broadcastStatus=complete"
                              f"&id={self.broadcast_id}&part=status")
            self.log("[youtube] broadcast ended")
        except Exception as e:
            self.log(f"[youtube] end failed: {e}")

    # ── live chat ────────────────────────────────────────────────────────────
    def _resolve_chat_id(self):
        if not self.broadcast_id:
            return
        try:
            d = self._api("GET", f"/liveBroadcasts?part=snippet&id={self.broadcast_id}")
            items = d.get("items", [])
            if items:
                self.live_chat_id = items[0].get("snippet", {}).get("liveChatId")
        except Exception:
            pass

    def chat_source(self):
        """Return a poll() -> list[ChatMessage] callable (self-throttled to YouTube's
        pollingInterval; first poll primes the page token and skips pre-live backlog)."""
        def _poll():
            if not self.live_chat_id:
                self._resolve_chat_id()
            if not self.live_chat_id:
                return []
            now = time.time()
            if now < self._chat_next:
                return []
            try:
                url = (f"/liveChat/messages?liveChatId={self.live_chat_id}"
                       f"&part=snippet,authorDetails&maxResults=50")
                if self._chat_token:
                    url += f"&pageToken={self._chat_token}"
                d = self._api("GET", url)
                self._chat_reads_today += 1
                self._chat_token = d.get("nextPageToken")
                yt_secs = float(d.get("pollingIntervalMillis", 5000)) / 1000.0
                self._chat_next = now + self._paced_chat_interval(now, yt_secs)
                if not self._chat_primed:          # skip history from before we went live
                    self._chat_primed = True
                    return []
                out = []
                for it in d.get("items", []):
                    sn = it.get("snippet", {})
                    if sn.get("type") != "textMessageEvent":
                        continue
                    out.append(ChatMessage(
                        id=it.get("id", ""),
                        author=it.get("authorDetails", {}).get("displayName", "viewer"),
                        text=sn.get("displayMessage", ""), ts=now))
                return out
            except Exception as e:
                msg = str(e)
                if "403" in msg and "quota" in msg.lower():
                    # Daily quota exhausted — stop hammering entirely until it resets at
                    # ~midnight Pacific (retrying every N minutes just fails N more times).
                    self._chat_reads_today = self._chat_daily_reads      # mark budget spent
                    wait = self._seconds_until_quota_reset(now)
                    self._chat_next = now + wait
                    self.log(f"[youtube] chat quota exhausted — pausing chat reads until reset "
                             f"(~{wait/3600:.1f}h). Request a YouTube API quota increase for "
                             f"sustained 24/7 chat; lower ANIMICA_STREAM_CHAT_DAILY_READS to "
                             f"spread the current quota further.")
                else:
                    self.log(f"[youtube] chat poll: {e}")
                    self._chat_next = now + 15
                return []
        return _poll

    def chat_sink(self):
        def _post(text: str):
            if not self.live_chat_id or not text:
                return
            try:
                self._api("POST", "/liveChat/messages?part=snippet", {
                    "snippet": {"liveChatId": self.live_chat_id, "type": "textMessageEvent",
                                "textMessageDetails": {"messageText": text[:200]}}})
            except Exception as e:
                self.log(f"[youtube] chat post: {e}")
        return _post

    # ── VOD upload (resumable) ───────────────────────────────────────────────
    def upload_video(self, path: str, title: str, description: str = "",
                     privacy: str = "public", tags=None) -> str:
        size = os.path.getsize(path)
        meta = {
            "snippet": {"title": title[:100], "description": description[:4900],
                        "tags": tags or ["Animica", "ANM", "AI", "livestream", "VOD"]},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        }
        loc = None
        for attempt in (0, 1):
            r = self._sess.post(
                UPLOAD + "?uploadType=resumable&part=snippet,status",
                headers={"authorization": f"Bearer {self.access}",
                         "content-type": "application/json; charset=UTF-8",
                         "x-upload-content-length": str(size),
                         "x-upload-content-type": "video/mp4"},
                data=_json.dumps(meta).encode(), timeout=30)
            if r.status_code == 401 and attempt == 0 and self._refresh_token():
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"YT upload init -> {r.status_code}: {r.text[:200]}")
            loc = r.headers.get("location")
            break
        if not loc:
            raise RuntimeError("YT upload: no resumable session URL returned")
        with open(path, "rb") as f:
            put = self._sess.put(loc, data=f, timeout=None,
                                 headers={"content-type": "video/mp4", "content-length": str(size)})
        if put.status_code >= 400:
            raise RuntimeError(f"YT upload PUT -> {put.status_code}: {put.text[:200]}")
        vid = (put.json() if put.content else {}).get("id", "")
        self.log(f"[youtube] uploaded VOD {vid} ({size // (1024*1024)} MiB)")
        return vid
