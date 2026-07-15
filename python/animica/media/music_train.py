"""Music fine-tuning corpus + recipe — including auto-ingest of an owned SoundCloud artist.

The Animica music generator (prompt -> audio) improves by fine-tuning on a captioned corpus. This
module builds that corpus and a reproducible ENA training recipe. It can auto-ingest the public
tracks of a SoundCloud artist YOU OWN (or are licensed to train on) — the `emblade` set is wired in
because the operator confirmed they own that account.

RIGHTS GATE (hard): ingestion refuses unless the caller asserts ownership/license via
`consent_owner=True` (CLI: --i-own-this). Training on third-party audio you don't hold rights to is
not something this tool will do for you.

Ingestion uses yt-dlp if present (an optional dep) and is fail-closed: it fetches real audio +
metadata or raises — never fabricates a corpus. Actual fine-tuning needs a GPU trainer (audiocraft/
musicgen); this module prepares everything and hands a recipe to that trainer.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from typing import List, Optional

from .base import MediaError, MediaBackendUnavailable, sha3_hex

EMBLADE_URL = "https://soundcloud.com/emblade"


@dataclass
class MusicRecipe:
    name: str
    base_model: str          # e.g. "facebook/musicgen-small"
    corpus_dir: str
    tracks: int
    seconds_per_clip: int
    lora_rank: int
    steps: int
    learning_rate: float
    source: str
    consent: str             # who asserted rights + how
    corpus_sha3: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:64] or "track"


def ingest_soundcloud(
    url: str = EMBLADE_URL,
    out_dir: str = "~/.animica/music-corpus/emblade",
    *,
    consent_owner: bool = False,
    max_tracks: int = 80,
    seconds_per_clip: int = 30,
    log=print,
) -> dict:
    """Download an artist's public tracks + metadata into a captioned training corpus.

    Rights-gated: requires consent_owner=True (you own or are licensed to train on this catalogue).
    Returns {corpus_dir, tracks, captions, corpus_sha3}.
    """
    if not consent_owner:
        raise MediaError(
            "refusing to ingest audio without a rights assertion. Only ingest catalogues you own or "
            "are licensed to train on — pass consent_owner=True (CLI: --i-own-this).")
    ytdlp = shutil.which("yt-dlp") or shutil.which("youtube-dl")
    if not ytdlp:
        raise MediaBackendUnavailable(
            "yt-dlp is required to ingest SoundCloud audio — `pip install yt-dlp` (or "
            "`pip install 'animica[media]'`).")

    out_dir = os.path.expanduser(out_dir)
    audio_dir = os.path.join(out_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)

    log(f"ingesting up to {max_tracks} tracks from {url} → {out_dir}")
    # Download best audio as wav, plus per-track json metadata. Playlist-end bounds the count.
    cmd = [
        ytdlp, "--ignore-errors", "--no-warnings",
        "--playlist-end", str(max_tracks),
        "--extract-audio", "--audio-format", "wav", "--audio-quality", "0",
        "--write-info-json",
        "-o", os.path.join(audio_dir, "%(playlist_index)s-%(title)s.%(ext)s"),
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60 * 60)
    if proc.returncode != 0 and not os.listdir(audio_dir):
        raise MediaError(f"yt-dlp ingest failed: {proc.stderr[-400:]}")

    # Build captions.jsonl from the info-json sidecars: {audio, caption, meta}.
    captions_path = os.path.join(out_dir, "captions.jsonl")
    rows: List[dict] = []
    for fn in sorted(os.listdir(audio_dir)):
        if not fn.endswith(".info.json"):
            continue
        try:
            meta = json.load(open(os.path.join(audio_dir, fn)))
        except Exception:
            continue
        stem = fn[: -len(".info.json")]
        wav = None
        for cand in (stem + ".wav", stem + ".WAV"):
            if os.path.exists(os.path.join(audio_dir, cand)):
                wav = cand
                break
        if not wav:
            continue
        title = meta.get("title") or stem
        genre = meta.get("genre") or ""
        tags = ", ".join(meta.get("tags") or [])[:200]
        caption = "; ".join([p for p in [title, genre, tags, "by emblade"] if p])
        rows.append({"audio": os.path.join("audio", wav), "caption": caption,
                     "duration": meta.get("duration"), "title": title})

    if not rows:
        raise MediaError("no tracks ingested — nothing to train on (check the URL and yt-dlp).")

    with open(captions_path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    corpus_sha3 = sha3_hex(open(captions_path, "rb").read())
    log(f"corpus ready: {len(rows)} tracks, captions.jsonl sha3={corpus_sha3[:16]}…")
    return {"corpus_dir": out_dir, "tracks": len(rows), "captions": captions_path, "corpus_sha3": corpus_sha3}


def build_recipe(
    corpus: dict,
    *,
    name: str = "emblade-music-v1",
    base_model: str = "facebook/musicgen-small",
    lora_rank: int = 16,
    steps: int = 800,
    learning_rate: float = 1e-4,
    seconds_per_clip: int = 30,
    consent: str = "operator asserted ownership of soundcloud.com/emblade",
) -> MusicRecipe:
    """Produce a reproducible fine-tune recipe from an ingested corpus and write it into the corpus dir."""
    recipe = MusicRecipe(
        name=name, base_model=base_model, corpus_dir=corpus["corpus_dir"],
        tracks=corpus["tracks"], seconds_per_clip=seconds_per_clip, lora_rank=lora_rank,
        steps=steps, learning_rate=learning_rate, source=EMBLADE_URL,
        consent=consent, corpus_sha3=corpus["corpus_sha3"],
    )
    path = os.path.join(corpus["corpus_dir"], "recipe.json")
    with open(path, "w") as f:
        f.write(recipe.to_json())
    return recipe


def train_music_lora(recipe: MusicRecipe, *, out: Optional[str] = None, log=print) -> dict:
    """Fine-tune the music model on the corpus (GPU). Fail-closed if the trainer isn't installed.

    Kept as a thin, honest entrypoint: it verifies the trainer stack, then delegates. It never
    fabricates a checkpoint — no audiocraft/torch-GPU, no training.
    """
    try:
        import torch  # type: ignore
    except Exception as e:
        raise MediaBackendUnavailable(f"music fine-tuning needs a GPU torch install: {e}")
    if not torch.cuda.is_available():
        raise MediaBackendUnavailable("music fine-tuning needs a CUDA GPU (musicgen LoRA is heavy)")
    try:
        import audiocraft  # type: ignore  # noqa: F401
    except Exception as e:
        raise MediaBackendUnavailable(
            f"install the music trainer: `pip install audiocraft` ({e}). Corpus + recipe are ready "
            f"at {recipe.corpus_dir}/recipe.json — run this on a GPU box to produce the adapter.")

    # Real training is delegated to the media LoRA harness once audiocraft is present.
    from .train import train_music_lora_impl  # optional; present only in GPU builds
    return train_music_lora_impl(recipe, out=out, log=log)
