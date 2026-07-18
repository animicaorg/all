# GPU Studios — homepage insertions for /var/www/animica.dev/index.html

Staged pages: `video/index.html`, `audio/index.html`, `render/index.html` (deploy to
`/var/www/animica.dev/{video,audio,render}/index.html`). Everything below is copy-paste
against the homepage as of 2026-07-18 (line numbers from that file). No CSS additions are
required — every snippet uses classes already defined in the homepage `<style>`.

---

## (a) Nav links — inside `.links` (index.html:289-298)

Insert the three studio links **after** `<a href="#studio">Media</a>` (line 291):

```html
    <a href="/video/">Video</a>
    <a href="/audio/">Audio</a>
    <a href="/render/">Render</a>
```

Result (context):

```html
  <div class="links">
    <a href="#chat">Chat</a>
    <a href="#studio">Media</a>
    <a href="/video/">Video</a>
    <a href="/audio/">Audio</a>
    <a href="/render/">Render</a>
    <a href="#agent">Agent</a>
    <a href="#build">Build</a>
    <a href="#agents">For agents</a>
    <a href="#api">API</a>
    <a href="#internet">Internet</a>
    <a href="/portal.html">Docs</a>
  </div>
```

---

## (b) Promo chip — sibling of the `🎨 Media studio ↓` chip (index.html:~329)

Paste **immediately after** the existing chip anchor (the one ending
`>🎨 Media studio ↓</a>` inside `.chat-head`). Blue accent to distinguish it from the
clay media chip:

```html
        <a href="/video/" title="Video, audio and Blender render tools on the GPU-miner network" style="font:inherit;font-size:12px;color:var(--blue);background:rgba(46,99,255,.07);border:1px solid rgba(46,99,255,.22);border-radius:8px;padding:5px 9px;cursor:pointer;text-decoration:none">⚡ GPU Studios</a>
```

---

## (c) `<section id="gpu">` — full three-card section

Paste **between** the `#studio` section's closing `</section>` (line ~423) and the
`aistrip` section (`<section style="padding-top:6px">`, line ~425):

```html
  <section id="gpu">
    <div class="kicker">GPU Studios · new in 9.0.0</div>
    <h2>Three studios. <span class="it">One GPU fleet.</span></h2>
    <p class="sub">Upload a file, and independent GPU miners do the heavy lifting — video finishing, audio post, and a distributed Blender render farm. Free, no key; jobs queue honestly and go through eventually. Miners earn ANM per job, settled on-chain from the block reward (≤20% of each block).</p>
    <div class="grid">
      <a class="card" href="/video/" style="display:block;color:inherit">
        <span class="ic">🎬</span><h3>Video Studio</h3>
        <p>Upscale to 4K, smooth to 60 fps, auto-subtitle, remove backgrounds, cut vertical shorts.</p>
        <p style="margin-top:10px;font-family:'JetBrains Mono';font-size:11.5px;color:var(--gold)">miners earn 1–3 ANM / job</p>
        <p style="margin-top:8px;color:var(--clay-ink);font-size:14px">Open the Video Studio →</p>
      </a>
      <a class="card" href="/audio/" style="display:block;color:inherit">
        <span class="ic">🎛️</span><h3>Audio Studio</h3>
        <p>Split stems, isolate vocals, denoise and master to LUFS targets — studio-grade, from one upload.</p>
        <p style="margin-top:10px;font-family:'JetBrains Mono';font-size:11.5px;color:var(--gold)">miners earn 0.5–1.5 ANM / job</p>
        <p style="margin-top:8px;color:var(--clay-ink);font-size:14px">Open the Audio Studio →</p>
      </a>
      <a class="card" href="/render/" style="display:block;color:inherit">
        <span class="ic">🧊</span><h3>Render Farm</h3>
        <p>Upload a packed .blend — frames render in parallel chunks on Cycles across GPU miners, assembled to MP4 or frames.</p>
        <p style="margin-top:10px;font-family:'JetBrains Mono';font-size:11.5px;color:var(--gold)">miners earn 2.5 ANM / chunk</p>
        <p style="margin-top:8px;color:var(--clay-ink);font-size:14px">Open the Render Farm →</p>
      </a>
    </div>
  </section>
```

(If a nav anchor to the section is wanted instead of the three direct links in (a), use
`<a href="#gpu">GPU Studios</a>` — but (a) as written links straight to the pages.)

---

## (d) Ecosystem grid — three entries in `.eco` (index.html:~560-569)

Insert **after** the `pool.animica.org` entry (or anywhere in the grid; the grid is
4-across so three new entries keep it balanced at 11 → consider placing before the
`Node upgrade` entry):

```html
      <a href="/video/"><div class="n">Video Studio</div><div class="d">Upscale · subtitles · shorts</div></a>
      <a href="/audio/"><div class="n">Audio Studio</div><div class="d">Stems · vocals · mastering</div></a>
      <a href="/render/"><div class="n">Render Farm</div><div class="d">Distributed Blender Cycles</div></a>
```

---

## openapi.json — path fragments to merge into `"paths"`

```json
{
  "/api/mkt/v1/media/uploads": {
    "post": {
      "summary": "Upload a binary input for a GPU Studios media job",
      "description": "Raw binary body (no multipart). Caps: video/blend 512 MB, audio/reference 200 MB. Unconsumed uploads expire after 4 h; inputs are wiped when the owning job delivers or expires.",
      "parameters": [
        {"name": "content-type", "in": "header", "required": true, "schema": {"type": "string"}, "description": "The file's MIME type, or application/octet-stream"},
        {"name": "x-anm-purpose", "in": "header", "required": true, "schema": {"type": "string", "enum": ["video", "audio", "blend", "reference"]}}
      ],
      "requestBody": {"required": true, "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}},
      "responses": {"200": {"description": "Stored", "content": {"application/json": {"schema": {"type": "object", "properties": {"upload_id": {"type": "string"}, "bytes": {"type": "integer"}, "sha3": {"type": "string"}}}}}}, "413": {"description": "File exceeds the size cap"}, "429": {"description": "Rate limited"}}
    }
  },
  "/api/mkt/v1/media/jobs": {
    "post": {
      "summary": "Submit a media job to the GPU-miner queue",
      "description": "GPU Studios kinds (9.0.0): video_upscale {scale 2|4, model fast|quality}, video_interpolate {factor 2|4}, video_subtitles {language, burn_in}, video_bgremove {mode green|alpha}, video_shorts {count 1-5, duration 10-60, aspect 9:16|1:1|16:9, subtitles}, audio_stems {format mp3|wav}, audio_isolate {format}, audio_enhance {denoise, loudness -30..-6, format}, audio_master {preset streaming|loud|podcast, format, reference_upload_id?}, render_blender {engine CYCLES, frame_start, frame_end, frame_step 1-10, resolution_percent 25-200, fps 6-60, output mp4|frames, samples? 16-2048; ≤2000 frames}. Plus the existing prompt kinds (image, video_t2v, video_multiscene, video_i2v, audio). Upload-consuming jobs reference inputs via upload_ids.",
      "requestBody": {"required": true, "content": {"application/json": {"schema": {"type": "object", "required": ["kind"], "properties": {"kind": {"type": "string"}, "params": {"type": "object"}, "upload_ids": {"type": "array", "items": {"type": "string"}}}}}}},
      "responses": {"200": {"description": "Queued", "content": {"application/json": {"schema": {"type": "object", "properties": {"job_id": {"type": "string"}}}}}}}
    }
  },
  "/api/mkt/v1/media/jobs/{id}": {
    "get": {
      "summary": "Poll a media job",
      "parameters": [{"name": "id", "in": "path", "required": true, "schema": {"type": "string"}}],
      "responses": {"200": {"description": "Job state", "content": {"application/json": {"schema": {"type": "object", "properties": {"status": {"type": "string", "enum": ["PENDING", "WAITING", "RUNNING", "DONE", "FAILED", "EXPIRED", "CANCELLED"]}, "progressPct": {"type": "number"}, "progressNote": {"type": "string"}, "minersOnline": {"type": "integer"}, "position": {"type": "integer"}, "chunks_done": {"type": "integer", "description": "render_blender parents only"}, "chunks_total": {"type": "integer", "description": "render_blender parents only"}, "result": {"type": "object", "description": "small results: {b64, mime, sha3}"}, "result_url": {"type": "string", "description": "large results: stream from /api/mkt/v1/media/artifacts/{id}"}, "error": {"type": "string"}}}}}}}
    }
  },
  "/api/mkt/v1/media/artifacts/{id}": {
    "get": {
      "summary": "Download a finished job's artifact",
      "description": "Streams the result with its MIME type and Content-Disposition. Addressed by job id (as handed out in result_url). Results are wiped on a TTL (6 h private / 24 h public).",
      "parameters": [{"name": "id", "in": "path", "required": true, "schema": {"type": "string"}}],
      "responses": {"200": {"description": "The artifact bytes"}, "404": {"description": "Unknown job or artifact already wiped"}}
    }
  },
  "/api/mkt/v1/media/capabilities": {
    "get": {
      "summary": "Live per-kind GPU-miner availability",
      "responses": {"200": {"description": "Counts of online miners per job kind", "content": {"application/json": {"schema": {"type": "object", "properties": {"kinds": {"type": "object", "additionalProperties": {"type": "integer"}}}}}}}}
    }
  }
}
```

---

## llms.txt — lines to append

```
## GPU Studios (9.0.0) — video / audio / Blender render tools on the GPU-miner network
- https://animica.dev/video/ : Video Studio — upscale (video_upscale), 60fps interpolation (video_interpolate), subtitles (video_subtitles), background removal (video_bgremove), auto-shorts (video_shorts)
- https://animica.dev/audio/ : Audio Studio — stems (audio_stems), vocal isolation (audio_isolate), enhance (audio_enhance), mastering (audio_master, optional reference upload)
- https://animica.dev/render/ : Render Farm — distributed Blender 4.2.9 Cycles (render_blender parent → render_chunk jobs → assemble); packed .blend in, MP4 or PNG-frames zip out
- POST https://animica.dev/api/mkt/v1/media/uploads : raw binary body + headers content-type and x-anm-purpose (video|audio|blend|reference) → {upload_id, bytes, sha3}. Caps 512 MB video/blend, 200 MB audio/reference; unconsumed uploads expire in 4 h.
- POST https://animica.dev/api/mkt/v1/media/jobs : {kind, params, upload_ids} → {job_id}. New kinds: video_upscale, video_interpolate, video_subtitles, video_bgremove, video_shorts, audio_stems, audio_isolate, audio_enhance, audio_master, render_blender.
- GET https://animica.dev/api/mkt/v1/media/jobs/{id} : poll status/progressPct/progressNote; render_blender parents also report {chunks_done, chunks_total}. Small results inline as result.b64; large results point at result_url.
- GET https://animica.dev/api/mkt/v1/media/artifacts/{id} : streams a finished job's file (mime + Content-Disposition); artifacts expire on a TTL.
- GET https://animica.dev/api/mkt/v1/media/capabilities : {kinds: {<kind>: <online miner count>}} — 0 means jobs queue until a miner connects (they are never dropped).
- No API key. All processing happens on independent GPU miners, which earn ANM IOUs settled on-chain from the block reward (≤20% of each block, from Animica 9.0.0). Run one: pip install -U animica && animica up
```

---

## Deploy reminders (not part of the splice)

- sw.js: bump `CACHE` to `anmdev-v4` (contract) so the new pages aren't shadowed.
- nginx already injects the upgrade banner — the staged pages intentionally do **not**
  include the banner `<script>` tag.
- nginx needs the 9.0.0 `location ^~` blocks for `/api/mkt/v1/media/uploads` (512m,
  request buffering off) and artifacts (proxy_buffering off) before the upload UI can
  take big files.
