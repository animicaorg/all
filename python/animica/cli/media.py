"""`animica media` — generative media (image/video/audio) provider commands.

Miners run `animica media serve` to earn by serving media jobs. Models install automatically on
`animica up` (see cli/up.py); `animica media install` does it explicitly. All heavy imports are lazy
so this module loads even when the optional `media` extra (diffusers) is not installed.
"""

from __future__ import annotations

import os
import shutil

import typer

app = typer.Typer(help="Generative media provider (image/video/audio).")

# Per-tier default models + approximate on-disk footprint (GB) for the disk guard.
MEDIA_MODEL_LADDER = {
    "image": {
        "standard": ("stabilityai/sd-turbo", 5.0),
        "premium": ("stabilityai/sdxl-turbo", 7.0),
        "elite": ("black-forest-labs/FLUX.1-schnell", 34.0),
    },
}


@app.command()
def doctor():
    """Check whether this machine can serve media jobs."""
    from animica.media.base import media_available

    avail, why = media_available()
    typer.echo(f"media backend : {'OK' if avail else 'UNAVAILABLE'} ({why})")
    try:
        import torch

        cuda = torch.cuda.is_available()
        typer.echo(f"torch         : {torch.__version__} (cuda={'yes' if cuda else 'no — CPU only, slow'})")
        if cuda:
            for i in range(torch.cuda.device_count()):
                p = torch.cuda.get_device_properties(i)
                typer.echo(f"  gpu[{i}]      : {p.name} {round(p.total_memory/1e9,1)}GB")
    except Exception as e:
        typer.echo(f"torch         : not importable ({e})")
    total, used, free = shutil.disk_usage(os.path.expanduser("~"))
    typer.echo(f"disk free     : {round(free/1e9,1)}GB of {round(total/1e9,1)}GB")
    if not avail:
        typer.echo("\nInstall the media extra:  pip install 'animica[media]'")


@app.command()
def models(kind: str = typer.Option("image", help="image|video|audio")):
    """List the model ladder for a media kind."""
    ladder = MEDIA_MODEL_LADDER.get(kind)
    if not ladder:
        typer.echo(f"no models registered for kind '{kind}' yet")
        raise typer.Exit(0)
    for tier, (model, gb) in ladder.items():
        typer.echo(f"{tier:9s} {model:44s} ~{gb}GB")


@app.command()
def install(
    kind: str = typer.Option("image", help="image|video|audio"),
    tier: str = typer.Option("standard", help="which tier's model to fetch (standard|premium|elite)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="skip the disk-space confirmation"),
):
    """Download the media model for a tier (disk-guarded)."""
    ladder = MEDIA_MODEL_LADDER.get(kind, {})
    entry = ladder.get(tier)
    if not entry:
        typer.echo(f"no model for kind={kind} tier={tier}")
        raise typer.Exit(1)
    model_id, gb = entry
    total, used, free = shutil.disk_usage(os.path.expanduser("~"))
    need = gb * 1.3  # headroom
    typer.echo(f"model {model_id} (~{gb}GB); free disk {round(free/1e9,1)}GB")
    if free / 1e9 < need:
        typer.echo(f"REFUSING: need ~{round(need,1)}GB free (headroom). Free some space first.")
        raise typer.Exit(1)
    if not yes:
        typer.confirm(f"Download {model_id} (~{gb}GB)?", abort=True)
    from huggingface_hub import snapshot_download

    typer.echo("downloading…")
    path = snapshot_download(model_id, allow_patterns=["*.json", "*.txt", "*.safetensors", "*.png"])
    typer.echo(f"done: {path}")


@app.command()
def gen(
    prompt: str = typer.Argument(..., help="text prompt"),
    out: str = typer.Option("out.png", help="output PNG path"),
    tier: str = typer.Option("standard"),
    width: int = typer.Option(512),
    height: int = typer.Option(512),
    seed: int = typer.Option(None),
):
    """Generate one image locally (fail-closed)."""
    from animica.media.image_gen import generate_image

    typer.echo(f"generating '{prompt[:60]}'…")
    res = generate_image(prompt, tier=tier, width=width, height=height, seed=seed)
    with open(out, "wb") as f:
        f.write(res["bytes"])
    typer.echo(f"wrote {out} ({len(res['bytes'])} bytes, {res['model']}, sha3={res['sha3'][:16]}…)")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(4610),
    register: bool = typer.Option(False, "--register", help="join the Animica media QUEUE and render jobs for the network (earns IOU rewards)"),
    gateway: str = typer.Option("https://animica.dev", "--gateway", help="gateway hosting the media queue"),
    token: str = typer.Option(None, "--token", help="reuse a miner token (else one is minted + saved to ~/.animica/media-miner.json)"),
    poll: float = typer.Option(3.0, "--poll", help="seconds between queue polls when idle"),
    once: bool = typer.Option(False, "--once", help="render a single job then exit (for testing)"),
):
    """Serve generative media.

    --register (recommended): become a media MINER — poll the Animica queue, render jobs on this
    box (image / video / multi-scene / image->video / music, per what's installed), and post them
    back. No model ever runs on the gateway; jobs wait in the queue until a miner like this claims
    them. Without --register this instead runs the standalone HTTP media worker on host:port.
    """
    if register:
        from animica.media.miner import run_miner, probe_capabilities
        caps = probe_capabilities()
        if not caps:
            typer.secho("This box can't serve any media kind yet.", fg="red")
            typer.echo("  • image->video: install ffmpeg")
            typer.echo("  • image + multi-scene: pip install 'animica[media]'")
            typer.echo("  • text->video / music (GPU): set ANIMICA_MEDIA_VIDEO_ENABLED=1 / ANIMICA_MEDIA_AUDIO_ENABLED=1")
            raise typer.Exit(1)
        typer.echo(f"media miner starting — capabilities: {', '.join(caps)}")
        run_miner(gateway, token=token, caps=caps, poll_interval=poll, once=once, log=typer.echo)
        return
    from animica.media.service import main as serve_main
    serve_main(host=host, port=port)


@app.command()
def train(
    dataset: str = typer.Argument(..., help="dir of images + captions (<stem>.txt or captions.jsonl)"),
    tier: str = typer.Option("standard", help="base model tier to fine-tune"),
    steps: int = typer.Option(400),
    rank: int = typer.Option(8, help="LoRA rank"),
    resolution: int = typer.Option(512),
    out: str = typer.Option(None, "--out", help="adapter output dir (defaults to the registry)"),
):
    """Train the image model further with LoRA and register the checkpoint (GPU; fail-closed)."""
    from animica.media.train import train_image_lora
    typer.echo(f"training LoRA on {dataset} (tier={tier}, steps={steps})…")
    rec = train_image_lora(dataset, tier=tier, steps=steps, rank=rank,
                           resolution=resolution, output_dir=out)
    typer.echo(f"trained adapter {rec['adapter_id']} "
               f"(avg_loss={rec.get('avg_loss')}, imgs={rec.get('images')}, {rec['size']} bytes)")


@app.command()
def checkpoints():
    """List trained media checkpoints in the local registry."""
    from animica.media import checkpoints as ck
    rows = ck.list_checkpoints()
    if not rows:
        typer.echo("no trained checkpoints yet — run `animica media train <dataset>`")
        return
    for r in rows:
        typer.echo(f"{r['adapter_id']}  {r.get('kind')}  base={r.get('base_model')}  "
                   f"{r.get('size')}B  {r.get('notes','')}")


@app.command()
def pull(
    adapter_id: str = typer.Argument(..., help="adapter id to download"),
    endpoint: str = typer.Option("http://127.0.0.1:4610", help="a media worker serving the checkpoint"),
):
    """Download + install a trained checkpoint from another media worker (serve it here)."""
    import urllib.request
    from animica.media import checkpoints as ck
    url = f"{endpoint.rstrip('/')}/v1/checkpoints/{adapter_id}/download"
    typer.echo(f"downloading {adapter_id} from {url}…")
    with urllib.request.urlopen(url, timeout=120) as resp:  # nosec B310
        blob = resp.read()
    rec = ck.install_from_bytes(blob)
    typer.echo(f"installed {rec['adapter_id']} ({rec['size']} bytes) — serve with --adapter {rec['adapter_id']}")


@app.command("ingest-music")
def ingest_music(
    url: str = typer.Option("https://soundcloud.com/emblade", "--url", help="SoundCloud artist/playlist URL"),
    out: str = typer.Option("~/.animica/music-corpus/emblade", "--out", help="corpus output dir"),
    i_own_this: bool = typer.Option(False, "--i-own-this", help="assert you own / are licensed to train on this catalogue (REQUIRED)"),
    max_tracks: int = typer.Option(80, "--max-tracks"),
    recipe: bool = typer.Option(True, "--recipe/--no-recipe", help="also write a fine-tune recipe.json"),
):
    """Ingest an OWNED SoundCloud catalogue into a captioned music training corpus (+ recipe).

    Rights-gated: only ingest audio you own or are licensed to train on (--i-own-this). Defaults to
    the emblade set, which the operator owns. Needs yt-dlp. Fail-closed — real audio or an error.
    """
    from animica.media.music_train import ingest_soundcloud, build_recipe
    corpus = ingest_soundcloud(url, out, consent_owner=i_own_this, max_tracks=max_tracks, log=typer.echo)
    typer.echo(f"corpus: {corpus['tracks']} tracks at {corpus['corpus_dir']} (sha3 {corpus['corpus_sha3'][:16]}…)")
    if recipe:
        r = build_recipe(corpus)
        typer.echo(f"recipe: {r.name} · base={r.base_model} · rank={r.lora_rank} · steps={r.steps} → {corpus['corpus_dir']}/recipe.json")
        typer.echo("train it on a GPU box: animica media train-music " + corpus['corpus_dir'])


@app.command("train-music")
def train_music(
    corpus_dir: str = typer.Argument(..., help="a corpus dir produced by `ingest-music` (contains recipe.json)"),
    out: str = typer.Option(None, "--out", help="adapter output dir"),
):
    """Fine-tune the music model on an ingested corpus (GPU; fail-closed). Prepares/uses recipe.json."""
    import json
    from animica.media.music_train import MusicRecipe, train_music_lora
    rp = os.path.join(os.path.expanduser(corpus_dir), "recipe.json")
    if not os.path.exists(rp):
        typer.secho(f"no recipe.json in {corpus_dir} — run `animica media ingest-music` first", fg="red")
        raise typer.Exit(1)
    recipe = MusicRecipe(**json.load(open(rp)))
    rec = train_music_lora(recipe, out=out, log=typer.echo)
    typer.echo(f"trained music adapter: {rec}")
