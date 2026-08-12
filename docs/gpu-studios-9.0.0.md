# Animica 9.0.0 — GPU Studios + on-chain IOU settlement

9.0.0 turns animica.dev into a place people come to for **GPU power**, not just AI
generation, and makes every service IOU **payable from the block reward** (never more
than 20% of a block). Three new products, all dispatch-only through the existing media
queue (no model or renderer ever runs on the gateway):

1. **Video Studio** (`/video`) — upscale (Real-ESRGAN 2×/4×), frame interpolation
   (RIFE, ffmpeg fallback), auto subtitles (Whisper → SRT + soft/burned track),
   background removal (green screen / alpha), auto-shorts (scene detection → vertical
   clips).
2. **Audio Studio** (`/audio`) — stem separation + vocal isolation (HDemucs via
   torchaudio), enhancement (denoise + loudness), mastering (reference spectral match +
   LUFS presets, own DSP).
3. **Render Farm** (`/render`) — upload a packed `.blend`; frames are split into chunk
   jobs rendered across GPU miners (Blender 4.2.9 auto-fetched, sha256-pinned,
   `--disable-autoexec`), then assembled to MP4/frames-zip by an assemble job.

## Job kinds (single source of truth: gateway `MEDIA_KINDS` + python `MediaKind`)

| kind | input | output | reward (IOU, ANM) | lease s |
|---|---|---|---|---|
| video_upscale | 1 video upload | mp4 | 3 | 900 |
| video_interpolate | 1 video upload | mp4 | 3 | 900 |
| video_subtitles | 1 video upload | zip {mp4, srt, txt} | 1 | 900 |
| video_bgremove | 1 video upload | mp4 (green) / webm (alpha) | 3 | 900 |
| video_shorts | 1 video upload | zip of mp4s | 2 | 900 |
| audio_stems | 1 audio upload | zip (4 stems) | 1.5 | 600 |
| audio_isolate | 1 audio upload | zip {vocals, instrumental} | 1 | 600 |
| audio_enhance | 1 audio upload | mp3/wav | 0.5 | 300 |
| audio_master | 1 audio (+opt reference) | mp3/wav | 0.5 | 300 |
| render_blender | 1 .blend upload | (parent — never claimable) | — | — |
| render_chunk | .blend via parent | zip of PNG frames | 2.5 | 1200 |
| render_assemble | chunk artifacts | mp4 or frames zip | 0.5 | 900 |

Rewards env-overridable per kind: `MEDIA_REWARD_<KIND>_NANM`.

## Params (server clamps; unlisted params dropped)

- video_upscale: `scale` 2|4 (2), `model` fast|quality (fast). Reject if output would
  exceed 3840×2160. Input ≤10 min.
- video_interpolate: `factor` 2|4 (2). Output fps ≤120. Input ≤10 min.
- video_subtitles: `language` auto|ISO-639-1 (auto), `burn_in` bool (true). ≤30 min.
- video_bgremove: `mode` green|alpha (green). ≤5 min.
- video_shorts: `count` 1..5 (3), `duration` 10..60 (30), `aspect` 9:16|1:1|16:9
  (9:16), `subtitles` bool (true). Input ≤60 min.
- audio_stems / audio_isolate: `format` mp3|wav (mp3). ≤20 min.
- audio_enhance: `denoise` bool (true), `loudness` −30..−6 LUFS (−16), `format`. ≤60 min.
- audio_master: `preset` streaming(−14)|loud(−9)|podcast(−16), `format`,
  `reference_upload_id?`. ≤60 min.
- render_blender: `engine` CYCLES only, `frame_start` (1), `frame_end` (=start),
  `frame_step` 1..10 (1), `resolution_percent` 25..200 (100), `fps` 6..60 (24),
  `output` mp4|frames (mp4; forced frames when 1 frame → PNG direct), `samples?`
  16..2048. Total frames ≤2000; chunk size `MEDIA_RENDER_CHUNK_FRAMES` (20) → ≤100
  chunks.

## Large-file plumbing (new — inputs/results were inline base64 in Postgres)

- **MediaUpload** model + disk store (`MEDIA_STORE_DIR`, default
  `<app>/var/media-store`): `POST /api/mkt/v1/media/uploads` streams a raw binary body
  (headers `content-type`, `x-anm-purpose` video|audio|blend|reference) → `{upload_id,
  bytes, sha3}`. Caps: video/blend 512 MB, audio/reference 200 MB; per-IP rate limit.
  TTL 4 h unconsumed; wiped when the owning job completes+delivers or expires. Miners
  fetch via `GET /api/mkt/v1/media/uploads/[id]` (Bearer miner token only).
- **Artifacts**: miners post big results as a raw stream to
  `POST /api/mkt/v1/media/miner/result-file?job_id=…` (headers `x-anm-mime`,
  `x-anm-sha3`, `x-anm-meta` b64-JSON ≤2 KB; server re-hashes while streaming,
  mismatch → 400, job stays RUNNING). Job gets `resultPath`/`resultBytes`; poll returns
  `result_url` → `GET /api/mkt/v1/media/artifacts/[jobId]` (public-by-job-id, streams
  with mime + Content-Disposition). Small results keep the existing b64 JSON path.
  Upload-consuming kinds are private (inputs wiped at terminal state); artifact results
  use TTL wipe (6 h private / 24 h public) instead of once-only delivery.
- **Progress + leases**: per-kind lease at claim (table above);
  `POST /api/mkt/v1/media/miner/progress {job_id, pct, note?}` extends the lease and
  surfaces real progress in poll (`progressPct/progressNote`).
- nginx: dedicated `location ^~` blocks for uploads + result-file
  (`client_max_body_size 512m`, `proxy_request_buffering off`) and artifacts
  (`proxy_buffering off`); existing `/api/mkt/` 24m block unchanged.

## Render-farm orchestration

`render_blender` submit creates the parent row (status **WAITING** — never claimable;
claim SQL only selects PENDING) + N `render_chunk` PENDING children (`parentId` set).
When the last chunk posts DONE the gateway enqueues one `render_assemble` job whose
params carry the chunk job ids; the assemble miner downloads the chunk zips via the
artifacts route, encodes, and posts the final artifact; the parent flips DONE pointing
at it (single-frame renders: parent DONE straight from the chunk PNG). A chunk FAILED
after max attempts fails the parent and CANCELs pending siblings. Parent poll
aggregates: `{chunks_done, chunks_total, pct}`.

Miner side (`media/render_farm.py`): resolve Blender = PATH → `ANIMICA_BLENDER` →
auto-download blender-4.2.9-linux-x64.tar.xz (sha256
`dfbc127a7d28f9c2175b23bf9d6701b2855f31eedfb391f9a6e60adb24572846`) into
`~/.animica/tools/`. Always `-b --disable-autoexec -noaudio` (untrusted .blend files
must never auto-run scripts); GPU via `--python-expr` OPTIX→CUDA→CPU ladder; parse
`Fra:` stdout lines → progress posts.

## Miner protocol additions (python/animica/media)

- `base.py`: new MediaKind values; `validate_magic` gains zip (PK\x03\x04), webm
  (EBML \x1aE\xdf\xa3), mp3 (ID3 or 0xFFEx frame sync).
- `net.py` (new): `GatewayClient` — streamed `download_input(url)`, `post_progress`,
  streamed `post_result_file(path, …)`; used only by the new kinds.
- `render_job(job, gw)`: new branches lazily import `video_studio` / `audio_studio` /
  `render_farm`; large outputs return `{"path": …}` and are streamed, small ones keep
  `{"b64": …}`.
- `probe_capabilities()`: video_upscale/interpolate/bgremove ⇐ CUDA ≥4 GiB (tri-state
  env `ANIMICA_MEDIA_VIDEOSTUDIO_ENABLED`); video_subtitles ⇐ transformers + (CUDA or
  env force); video_shorts ⇐ ffmpeg only; audio_stems/isolate ⇐ torchaudio + (CUDA
  ≥4 GiB or env `ANIMICA_MEDIA_AUDIOSTUDIO_ENABLED`); audio_enhance/master ⇐
  noisereduce+pyloudnorm+ffmpeg (CPU fine); render_chunk ⇐ Blender resolvable + (CUDA
  or `ANIMICA_RENDER_CPU=1`); render_assemble ⇐ ffmpeg.
- Model weights: Real-ESRGAN (SRVGGNetCompact fast / RRDBNet quality) from the
  official GitHub release URLs, RIFE flownet 4.13.2 safetensors from HF
  `imaginairy/rife-interpolation` @ `26442e52cc30b88c5cb490702647b8de9aaee8a7` — all
  sha256-pinned, cached under `~/.animica/media-weights/`, arch code vendored (torch
  only, no basicsr/realesrgan deps). Whisper via transformers; person segmentation via
  torchvision DeepLabV3; stems via `torchaudio.pipelines.HDEMUCS_HIGH_MUSDB_PLUS`.
- New base deps: `torchaudio>=2.6.0`, `torchvision>=0.21.0`, `noisereduce>=3.0.0`,
  `pyloudnorm>=0.1.1` (all plain universal wheels).

## On-chain IOU settlement (realizes the 50,000 fork — user directive)

Consensus mechanism already in tree (`consensus/iou_settlement.py` + hook in
`core/chain/block_import.py`, fork key `FORK_VPN_RELAY_REWARDS` = `FORK_IOU_SETTLEMENT`
@ mainnet **50,000**): settlement anchors are ordinary treasury-signed TRANSFER txs
carrying `ANMSETL1{"v":1,"pay":[[anim1…,nanm],…]}`; blocks at/after 50,000 carve the
anchored payouts **from the miner subsidy** (never minted), capped at 50 ANM/block
halving with emission — a constant **16.67% of the block subsidy, always < the 20%
ceiling**. 9.0.0 adds everything around it:

- `consensus/tests/test_iou_settlement.py`: strict-parse/encode round-trips, authority
  binding, scale/cap invariants (never exceeds cap or miner subsidy; miner+settlement
  == pre-fork miner output), **≤20%-of-block invariant across heights and halvings**,
  pre-fork inertness, pinned-checkpoint non-interference.
- `animica settle` CLI (`python/animica/cli/settle.py`): build/inspect/post anchors
  from the operator wallet (settlement authority = foundation treasury), refusing
  anchors that exceed the current cap or pre-fork height.
- Gateway worker `scripts/iou_settlement_worker.py` + systemd timer: reads the
  marketplace DB IOU ledgers — `MediaMiner.rewardNanm` (payout → `.address`),
  `VpnExit.rewardNanm` (→ owner Account.address), `HostingPin.rewardNanm` (→ account
  address) — computes payable = reward − settled ≥ 5 ANM, posts one ≤-cap anchor per
  cycle **only at/after fork height**, marks `settledNanm` after on-chain confirmation.
  (`VpnSession.rewardNanm` is per-session detail — settle exits only, pending
  double-count verification. AICF worker IOUs join when a payable ledger exists.)
- Marketplace schema: `settledNanm BigInt @default(0)` on MediaMiner/VpnExit/HostingPin;
  register/me endpoints expose accrued vs settled.

## Frontend (animica.dev)

Static pages `/video/`, `/audio/`, `/render/` in the house warm-paper design (no
collision with :4950 proxy prefixes); XHR binary uploads with progress; poll with real
`progressPct`; artifact downloads + inline preview via `result_url`; per-kind miner
availability from `/api/mkt/v1/media/capabilities`; auto-resubmit on FAILED (≤3 cycles,
reusing the surviving upload). Homepage: nav links, promo chips, "GPU Studios" section,
ecosystem cards. `openapi.json` + `llms.txt` document the new endpoints; sw.js CACHE →
`anmdev-v4`; banner → 9.0.0 `?v=900`.

## Also in 9.0.0

- Fix `image_gen.py:232` NameError (`_drop_pipeline` → `_reclaim_all_vram()`) — the
  8.4.1 OOM downshift path crashed instead of retrying frugally.
