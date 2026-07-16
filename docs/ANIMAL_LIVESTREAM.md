# Animica Animal — 24/7 AI Livestream

Animica Animal is a self-contained pipeline that runs an **animated character live on YouTube around
the clock**. It renders the character in real time, **reads the YouTube live chat and answers out loud**
in-character, moves and does quirky idle behaviors, overlays live Animica network stats, and
**auto-uploads every 1-hour segment as a VOD** so your channel fills itself.

The character is fully editable: restyle it by chat, tune its palette and voice, upload your own PNG
mascot, and give it a private knowledge base (RAG) so it answers with facts unique to your world.
Animica ships with **Momo the ginger cat** as the default; end users make it theirs.

- Console + connect + character studio: **https://animica.dev/animal**
- Runs the render on **your own machine** (a GPU box, or CPU for a lighter render).
- Everything is honest: it posts only through the **official YouTube Data API** to a channel **you own**.
  AI/synthetic content is disclosed to YouTube on the broadcast.

---

## 1. Quick start

```bash
pip install -U animica

# Render a local preview first — no account needed:
animica animal stream --preview out.mp4 --seconds 20

# Go live (after connecting YouTube in the console, see §3):
animica animal stream --youtube --record-dir ./vods
```

`animica animal stream --help` lists every flag (resolution, fps, voice, music, bitrate, RTMP).

Key flags:

| Flag | Meaning |
|------|---------|
| `--preview out.mp4` | Render to a local file instead of going live (great for testing). |
| `--youtube` | Auto-create a 24/7 YouTube broadcast from the connected account. |
| `--rtmp rtmp://…` | Stream to a manual RTMP ingest URL (any platform) instead of auto-YouTube. |
| `--record-dir DIR` | Where 1-hour VOD segments are written (auto with `--youtube`). |
| `--seconds N` | Stop after N seconds (0 = run forever). |
| `--width/--height/--fps` | Frame geometry (default 1280×720@24). |
| `--voice` | `animalese` (default, dependency-free) · `piper` (natural TTS if installed) · `off`. |
| `--music` | `auto` (generated lo-fi bed) · a path to your own audio file · `off`. |

The worker pulls the **live-editable character** and your **Google OAuth tokens** from the console
automatically (localhost-only internal API). It re-serves and self-heals on transient failures before
falling back, so a 24/7 run keeps going.

---

## 2. What the audience sees

- A continuously animated character (the cat, or your PNG), lip-synced to its own speech.
- Idle behaviors: wander, zoomies, tail-chase, groom, nap, pounce, stretch, peek, sit.
- A HUD with live Animica network stats (block height, ANM price, peers, hashrate).
- Real-time chat interaction: viewers type in YouTube chat, the character reads it, answers **out loud**
  with an on-screen caption, and can also reply back into the chat.

The brain grounds replies in real ecosystem facts and the character's knowledge base; it never invents
metrics. If no LLM endpoint is reachable it falls back to in-character rule-based lines.

---

## 3. Connect YouTube (Google OAuth setup)

To let the pipeline create broadcasts and upload VODs on your channel, you register a Google OAuth app
**once** and drop its client id/secret into the marketplace env.

1. Go to **https://console.cloud.google.com** → create (or pick) a project.
2. **APIs & Services → Library →** enable **YouTube Data API v3**.
3. **APIs & Services → OAuth consent screen →** configure it (External is fine), add your Google
   account as a **Test user** while in testing.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID → Web application.**
   Add this **Authorized redirect URI** exactly:
   ```
   https://animica.dev/api/mkt/v1/animal/connect/youtube/callback
   ```
5. Copy the **Client ID** and **Client secret** into the marketplace `.env.production`:
   ```env
   YOUTUBE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com
   YOUTUBE_CLIENT_SECRET=xxxxxxxx
   ```
   Restart the marketplace service.
6. Open **https://animica.dev/animal**, sign in, and in **Connect socials** click **Connect** on the
   YouTube card. Approve the scopes (manage live broadcasts + upload). Done.

Scopes requested: `youtube` + `youtube.force-ssl` (live broadcast control + live chat), with
`access_type=offline` so a **refresh token** is stored — the worker refreshes access tokens itself for
unattended 24/7 operation. Tokens are sealed (AES-GCM) at rest; the internal API that unseals them is
Bearer-gated **and** only reachable on `127.0.0.1`.

---

## 4. Character studio

Everything is in the console at **animica.dev/animal → Character studio**.

- **Redesign by chat** — e.g. *"make her a sassy blue dragon who loves DeFi"*. Prefers the free
  Animica `/v1` LLM to turn the instruction into a patch, with a heuristic fallback.
- **Palette** — pick fur / dark fur / belly / eyes / accent colors.
- **Voice** — pitch and speech pace sliders.
- **Upload PNG mascot** — a transparent-background PNG (≤ 6 MiB) becomes the on-screen sprite. The image
  is stored content-addressed and the worker downloads it; **use built-in cat** or **Reset to Momo**
  reverts.
- **Knowledge base** — upload `.txt/.md/.csv/.json` docs or paste notes. Text is chunked, embedded, and
  stored under the character's `knowledge_ref`; the livestream brain retrieves from it (cosine over
  embeddings, keyword fallback) to answer chat with facts unique to your world. **Clear all** wipes it.

The character sheet mirrors the Python `Character` dataclass, so the worker renders exactly what you
edit — no redeploy needed; changes take effect on the next stream (and mid-stream for chat/knowledge).

---

## 5. Hourly VOD segments

While live, the pipeline tees the encoded stream to both the RTMP ingest **and** a rolling set of
`seg_00001.mp4`, `seg_00002.mp4`, … files in `--record-dir` (one hour each). A background uploader
publishes each completed segment as an **unlisted VOD** on your channel and deletes it after a
successful upload, so disk stays bounded. On stop it flushes the final partial segment.

---

## 6. Live status

While streaming, the worker heartbeats status (live/offline, viewers, uptime, watch URL, character
name) to the console every ~20s. The console **Live studio** panel and the animica.dev homepage show a
**● LIVE** badge with a **Watch on YouTube** link; the badge auto-clears if the worker stops
heartbeating for 90s.

---

## 7. Selling it — $350/month via PayPal

The public `/animal` page shows the product pitch and a **$350/month** Subscribe button. To turn the
button live:

1. In the **PayPal** dashboard → **Pay & Get Paid → Subscriptions**, create a product and a
   **$350 USD / month** recurring plan. Copy its **plan id** (`P-XXXXXXXXXXXXXXXX`).
2. In the marketplace `.env.production`:
   ```env
   ANIMAL_PRICE_USD=350
   PAYPAL_PLAN_ID=P-XXXXXXXXXXXXXXXX
   # or, for a fully hosted link: PAYPAL_SUBSCRIBE_URL=https://www.paypal.com/…
   ```
   Restart the marketplace. The public `GET /api/mkt/v1/animal/pricing` flips `configured:true` and the
   Subscribe button becomes a live PayPal checkout link.

Until a plan id is set, the button stays disabled and shows a "contact to subscribe" note (honest — no
dead button).

---

## 8. How it works (architecture)

```
character sheet + Google tokens (console, localhost-only internal API)
        │
        ▼
 render loop (PIL) ── behavior state machine ── voice (animalese/piper) ── audio mixer
        │  RGB frames                              │ s16le stereo
        ▼                                          ▼
   two writer threads → named FIFOs → single ffmpeg (bundled via imageio-ffmpeg)
        │
        ├─ [f=flv]  → YouTube RTMP ingest (live)
        └─ [f=segment: 3600s] → seg_%05d.mp4 → hourly VOD uploader

 brain: YouTube live chat → (LLM or rule-based) → spoken line + on-screen caption + chat reply
 heartbeat: live status → console → homepage "● LIVE" badge
```

Notes for operators:
- ffmpeg opens both raw inputs; the pipeline feeds them from **two threads with bounded queues** and
  passes `-analyzeduration 0 -probesize 32` per input so it never stalls on the analyze window.
- No AI model runs on the marketplace/gateway box — the render happens on **your** machine; LLM calls go
  to the free Animica `/v1` (or a URL you set via `ANIMICA_STREAM_LLM_URL`).
- Guardrails: owned-accounts only, official APIs only, AI content disclosed to YouTube.

---

## 9. Environment reference

| Var | Where | Purpose |
|-----|-------|---------|
| `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` | marketplace | Google OAuth app for live + upload. |
| `ANIMAL_INTERNAL_TOKEN` | marketplace + worker | Bearer for the localhost internal API. |
| `ANIMAL_MKT_URL` | worker | Console base (default `http://127.0.0.1:4950`). |
| `ANIMICA_STREAM_LLM_URL` | worker | Override the brain's LLM endpoint (default free `/v1`). |
| `ANIMAL_PRICE_USD` | marketplace | Displayed price (default 350). |
| `PAYPAL_PLAN_ID` / `PAYPAL_SUBSCRIBE_URL` | marketplace | Turns the Subscribe button live. |
