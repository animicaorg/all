"""
ENA CLI — agent, retrieval, useful-work, and training orchestration.

All commands live under ``animica ena``. Use ``--json`` (set on the ``ena``
group) for scriptable output. See docs/ena/cli.md for the full reference.
"""

from __future__ import annotations

import json as _json
import sys
from typing import Any, Optional

import typer
from rich.console import Console
from rich.table import Table

console = Console()

app = typer.Typer(name="ena", help="ENA agent / retrieval / useful-work / training.",
                  no_args_is_help=True, add_completion=False)


# ---------------------------------------------------------------------------
# Shared state + helpers
# ---------------------------------------------------------------------------

class _State:
    config: Optional[str] = None
    json: bool = False


_state = _State()


@app.callback()
def _root(config: Optional[str] = typer.Option(None, "--config",
          help="Path to an ENA config file", envvar="ANIMICA_ENA_CONFIG"),
          json_output: bool = typer.Option(False, "--json",
          help="Emit JSON instead of formatted text")) -> None:
    _state.config = config
    _state.json = json_output


def _ena():
    from animica.ena import ENA
    from animica.ena.errors import ENAError
    try:
        return ENA(config_path=_state.config)
    except ENAError as exc:
        console.print(f"[red]{exc.render()}[/red]")
        raise typer.Exit(1)


def _emit(data: Any, *, title: Optional[str] = None) -> None:
    if _state.json:
        console.print_json(_json.dumps(data, default=str))
        return
    if title:
        console.print(f"[bold cyan]{title}[/bold cyan]")
    if isinstance(data, str):
        console.print(data)
    else:
        console.print_json(_json.dumps(data, default=str))


def _guard(fn, *args, **kwargs):
    from animica.ena.errors import ENAError
    try:
        return fn(*args, **kwargs)
    except ENAError as exc:
        console.print(f"[red]{exc.render()}[/red]")
        raise typer.Exit(1)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)


# ===========================================================================
# models
# ===========================================================================

models_app = typer.Typer(help="Inspect and smoke-test model providers.", no_args_is_help=True)
app.add_typer(models_app, name="models")


@models_app.command("list")
def models_list(remote: bool = typer.Option(False, "--remote", help="Probe each endpoint")):
    e = _ena()
    rows = e.list_model_providers()
    if remote:
        for r in rows:
            r["probe"] = _guard(e.test_model, r["name"])
    if _state.json:
        return _emit({"providers": rows, "default": e.cfg.default_model_provider})
    table = Table("name", "provider", "transport", "model")
    for r in rows:
        table.add_row(r["name"], r["provider"], r["transport"], r["model"])
    console.print(table)
    console.print(f"default: [green]{e.cfg.default_model_provider}[/green]")


@models_app.command("test")
def models_test(provider: Optional[str] = typer.Option(None, "--provider"),
                model: Optional[str] = typer.Option(None, "--model")):
    e = _ena()
    if model and provider and provider in e.cfg.model_providers:
        e.cfg.model_providers[provider].model = model
    _emit(_guard(e.test_model, provider), title="model test")


# ===========================================================================
# embeddings
# ===========================================================================

emb_app = typer.Typer(help="Embedding provider tools.", no_args_is_help=True)
app.add_typer(emb_app, name="embeddings")


@emb_app.command("test")
def embeddings_test(provider: Optional[str] = typer.Option(None, "--provider")):
    _emit(_guard(_ena().test_embedding, provider), title="embedding test")


# ===========================================================================
# agent: ask / plan / run / chat
# ===========================================================================

@app.command("ask")
def ask(question: str = typer.Argument(...),
        context: Optional[str] = typer.Option(None, "--context"),
        model_provider: Optional[str] = typer.Option(None, "--model-provider"),
        model: Optional[str] = typer.Option(None, "--model"),
        embedding_provider: Optional[str] = typer.Option(None, "--embedding-provider")):
    e = _ena()
    out = _guard(e.agent.ask, question, context=context, model_provider=model_provider,
                 model=model, embedding_provider=embedding_provider)
    if _state.json:
        return _emit(out)
    console.print(out["answer"])
    if out["sources"]:
        console.print(f"\n[dim]sources: {', '.join(out['sources'])}[/dim]")


@app.command("plan")
def plan(goal: str = typer.Argument(...),
         context: Optional[str] = typer.Option(None, "--context"),
         model_provider: Optional[str] = typer.Option(None, "--model-provider"),
         model: Optional[str] = typer.Option(None, "--model")):
    e = _ena()
    out = _guard(e.agent.plan, goal, context=context, model_provider=model_provider, model=model)
    _emit(out["plan"] if not _state.json else out)


@app.command("run")
def run_task(task: str = typer.Argument(..., help="task file (.yaml/.json/.txt)"),
             model_provider: Optional[str] = typer.Option(None, "--model-provider"),
             model: Optional[str] = typer.Option(None, "--model")):
    e = _ena()
    out = _guard(e.agent.run_task, task, model_provider=model_provider, model=model)
    _emit(out if _state.json else out["answer"])


agent_app = typer.Typer(help="Agent subcommands.", no_args_is_help=True)
app.add_typer(agent_app, name="agent")
agent_app.command("run")(run_task)


@app.command("chat")
def chat(repo: Optional[str] = typer.Option(None, "--repo"),
         model_provider: Optional[str] = typer.Option(None, "--model-provider"),
         model: Optional[str] = typer.Option(None, "--model")):
    e = _ena()
    console.print("[bold]ENA chat[/bold] — type 'exit' to quit.")
    while True:
        try:
            q = console.input("[cyan]you> [/cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in ("exit", "quit", ""):
            break
        out = _guard(e.agent.ask, q, context=repo, model_provider=model_provider, model=model)
        console.print(f"[green]ena>[/green] {out['answer']}")


@app.command("code")
def code(task: str = typer.Argument(..., help="the coding task to perform"),
         workdir: str = typer.Option(".", "--workdir", help="sandbox working directory"),
         base_url: Optional[str] = typer.Option(None, "--base-url",
             help="OpenAI-compatible endpoint (e.g. the pool's served model)"),
         model: Optional[str] = typer.Option(None, "--model"),
         api_key_env: Optional[str] = typer.Option(None, "--api-key-env"),
         model_provider: Optional[str] = typer.Option(None, "--model-provider"),
         steps: int = typer.Option(24, "--steps", help="max tool steps")):
    """Run the ENA-native coding agent (tool loop) on the pool's served model."""
    res = _guard(_ena().code, task, workdir=workdir, base_url=base_url, model=model,
                 api_key_env=api_key_env, model_provider=model_provider, max_steps=steps)
    _emit(res if _state.json else {"status": res["status"], "steps": res["steps"],
                                   "summary": res["summary"]}, title="code")


# ===========================================================================
# index / search
# ===========================================================================

index_app = typer.Typer(help="Retrieval index management.", no_args_is_help=True)
app.add_typer(index_app, name="index")


@index_app.command("build")
def index_build(path: str = typer.Argument(...),
                name: Optional[str] = typer.Option(None, "--name"),
                embedding_provider: Optional[str] = typer.Option(None, "--embedding-provider")):
    e = _ena()
    idx = name or "default"
    _emit(_guard(e.build_index, [path], name=idx, embedding_provider=embedding_provider),
          title="index build")


@index_app.command("rebuild")
def index_rebuild(source: str = typer.Argument(...),
                  name: str = typer.Option("default", "--name"),
                  embedding_provider: Optional[str] = typer.Option(None, "--embedding-provider")):
    _emit(_guard(_ena().build_index, [source], name=name,
                 embedding_provider=embedding_provider), title="index rebuild")


@index_app.command("list")
def index_list():
    e = _ena()
    rows = e.store.list_indexes()
    if _state.json:
        return _emit({"indexes": rows})
    table = Table("name", "provider", "model", "dims", "chunks")
    for r in rows:
        table.add_row(r["name"], r["embedding_provider"], r["embedding_model"],
                      str(r["dimensions"]), str(r["chunk_count"]))
    console.print(table)


@app.command("search")
def search(query: str = typer.Argument(...),
           hybrid: bool = typer.Option(False, "--hybrid"),
           semantic: bool = typer.Option(False, "--semantic"),
           keyword: bool = typer.Option(False, "--keyword"),
           index: Optional[str] = typer.Option(None, "--index"),
           embedding_provider: Optional[str] = typer.Option(None, "--embedding-provider"),
           limit: int = typer.Option(8, "--limit")):
    mode = "keyword" if keyword else ("semantic" if semantic else "hybrid")
    e = _ena()
    hits = _guard(e.search, query, mode=mode, index=index,
                  embedding_provider=embedding_provider, limit=limit)
    if _state.json:
        return _emit({"results": hits})
    for h in hits:
        console.print(f"[green]{h['score']:.3f}[/green] [dim]{h['source']}[/dim]")
        console.print(f"  {h['text'][:160]}")


# ===========================================================================
# datasets
# ===========================================================================

ds_app = typer.Typer(help="Dataset preparation pipeline.", no_args_is_help=True)
app.add_typer(ds_app, name="datasets")


@ds_app.command("ingest")
def ds_ingest(path: str = typer.Argument(...), kind: str = typer.Option("scrape_records", "--kind")):
    from animica.ena import datasets as d
    _emit(_guard(d.ingest, path, kind, _ena().store), title="ingest")


@ds_app.command("normalize")
def ds_normalize(path: str = typer.Argument(...), out: str = typer.Option(..., "--out")):
    from animica.ena import datasets as d
    _emit(_guard(d.normalize, path, out), title="normalize")


@ds_app.command("dedupe")
def ds_dedupe(path: str = typer.Argument(...), out: str = typer.Option(..., "--out")):
    from animica.ena import datasets as d
    _emit(_guard(d.dedupe, path, out), title="dedupe")


@ds_app.command("split")
def ds_split(path: str = typer.Argument(...), out_dir: str = typer.Option(..., "--out-dir")):
    from animica.ena import datasets as d
    _emit(_guard(d.split, path, out_dir), title="split")


@ds_app.command("validate")
def ds_validate(path: str = typer.Argument(...)):
    from animica.ena import datasets as d
    res = _guard(d.validate, path)
    _emit(res)
    if not res["valid"]:
        raise typer.Exit(1)


@ds_app.command("contribute")
def ds_contribute(file: Optional[str] = typer.Argument(None, help="JSONL file of training rows"),
                  url: Optional[str] = typer.Option(None, "--url", help="URL to scrape into data"),
                  name: Optional[str] = typer.Option(None, "--name"),
                  kind: str = typer.Option("contributed", "--kind"),
                  no_curate: bool = typer.Option(False, "--no-curate")):
    """Contribute training data: a local JSONL file or a --url to scrape."""
    e = _ena()
    rows = None
    if file:
        from animica.ena import datasets as d
        rows = list(_guard(lambda: list(d.read_jsonl(file))))
    if not rows and not url:
        console.print("[red]provide a JSONL file or --url[/red]")
        raise typer.Exit(1)
    _emit(_guard(e.contribute_dataset, name=name, kind=kind, rows=rows, url=url,
                 curate=not no_curate), title="contributed")


@ds_app.command("list")
def ds_list():
    e = _ena()
    rows = e.list_datasets()
    if _state.json:
        return _emit({"datasets": rows})
    table = Table("dataset_id", "kind", "rows", "sha256")
    for r in rows:
        table.add_row(r.get("name", r["dataset_id"]), r["kind"], str(r.get("row_count", 0)),
                      (r.get("sha256") or "")[:16])
    console.print(table)


@ds_app.command("export")
def ds_export(path: str = typer.Argument(...), out: str = typer.Option(..., "--out"),
              fmt: str = typer.Option("jsonl", "--format")):
    from animica.ena import datasets as d
    _emit(_guard(d.export, path, out, fmt), title="export")


# ===========================================================================
# jobs (useful-work)
# ===========================================================================

jobs_app = typer.Typer(help="Useful-work job lifecycle.", no_args_is_help=True)
app.add_typer(jobs_app, name="jobs")


@jobs_app.command("create")
def jobs_create(type: str = typer.Option(..., "--type"),
                source: Optional[str] = typer.Option(None, "--source"),
                dataset: Optional[str] = typer.Option(None, "--dataset"),
                label: list[str] = typer.Option(None, "--label"),
                name: Optional[str] = typer.Option(None, "--name"),
                base_model: Optional[str] = typer.Option(None, "--base-model")):
    params: dict[str, Any] = {}
    if source:
        params["source"] = source
    if dataset:
        params["dataset"] = dataset
    if label:
        params["labels"] = list(label)
    if name:
        params["name"] = name
    if base_model:
        params["base_model"] = base_model
    job = _guard(_ena().jobs.create, type, params)
    _emit(job if _state.json else {"job_id": job["job_id"], "job_hash": job["job_hash"],
                                   "aicf_task_id": job["aicf_task_id"], "status": job["status"]})


@jobs_app.command("claim")
def jobs_claim(worker_id: str = typer.Option(..., "--worker-id"),
               types: Optional[str] = typer.Option(None, "--types")):
    tl = [t.strip() for t in types.split(",")] if types else None
    job = _guard(_ena().jobs.claim, worker_id, tl)
    _emit(job or {"claimed": None})


@jobs_app.command("run")
def jobs_run(job_id: Optional[str] = typer.Argument(None),
             worker_id: Optional[str] = typer.Option(None, "--worker-id"),
             types: Optional[str] = typer.Option(None, "--types")):
    tl = [t.strip() for t in types.split(",")] if types else None
    job = _guard(_ena().jobs.run, job_id, worker_id=worker_id, job_types=tl)
    _emit(job if _state.json else {"job_id": job["job_id"], "status": job["status"],
                                   "result": job.get("result")})


@jobs_app.command("submit")
def jobs_submit(job_id: str = typer.Argument(...), result: str = typer.Argument(...)):
    _emit(_guard(_ena().jobs.submit, job_id, result), title="submitted")


@jobs_app.command("verify")
def jobs_verify(job_id: str = typer.Argument(...)):
    job = _guard(_ena().jobs.verify, job_id)
    _emit(job if _state.json else {"job_id": job["job_id"], "status": job["status"],
                                   "verification": job.get("verification")})


@jobs_app.command("receipt")
def jobs_receipt(job_id: str = typer.Argument(...)):
    _emit(_guard(_ena().jobs.receipt, job_id))


@jobs_app.command("export-onchain")
def jobs_export(job_id: str = typer.Argument(...)):
    _emit(_guard(_ena().jobs.export_onchain, job_id))


@jobs_app.command("list")
def jobs_list(status: Optional[str] = typer.Option(None, "--status"),
              type: Optional[str] = typer.Option(None, "--type")):
    e = _ena()
    rows = e.jobs.list(status=status, job_type=type)
    if _state.json:
        return _emit({"jobs": rows})
    table = Table("job_id", "type", "status", "worker")
    for r in rows:
        table.add_row(r["job_id"], r["job_type"], r["status"], r.get("worker_id") or "-")
    console.print(table)


@jobs_app.command("show")
def jobs_show(job_id: str = typer.Argument(...)):
    _emit(_guard(_ena().jobs.get, job_id))


# ===========================================================================
# train
# ===========================================================================

train_app = typer.Typer(help="Training orchestration.", no_args_is_help=True)
app.add_typer(train_app, name="train")


@train_app.command("prepare")
def train_prepare(dataset: str = typer.Option(..., "--dataset"),
                  out: str = typer.Option(..., "--out"),
                  base_model: str = typer.Option("tiny-local-model", "--base-model"),
                  backend: str = typer.Option("command", "--backend"),
                  method: str = typer.Option("sft", "--method",
                      help="sft | lora | qlora | dpo | distill (python_transformers)"),
                  auto_split: bool = typer.Option(False, "--auto-split"),
                  launcher_command: Optional[str] = typer.Option(None, "--launcher-command"),
                  output_dir: Optional[str] = typer.Option(None, "--output-dir")):
    out_man = _guard(_ena().train_prepare, dataset=dataset, out=out, base_model=base_model,
                     backend=backend, method=method, auto_split=auto_split,
                     launcher_command=launcher_command, output_dir=output_dir)
    _emit(out_man if _state.json else {"out": out, "run_name": out_man["run_name"],
                                       "backend": out_man["backend"]}, title="manifest")


@train_app.command("run")
def train_run(manifest: str = typer.Option(..., "--manifest"),
              backend: Optional[str] = typer.Option(None, "--backend")):
    rec = _guard(_ena().train_run, manifest_path=manifest, backend=backend)
    _emit(rec if _state.json else {"run_id": rec["run_id"], "status": rec["status"],
                                   "error": rec.get("error")}, title="train run")


@train_app.command("eval")
def train_eval(manifest: str = typer.Option(..., "--manifest"),
               model_provider: Optional[str] = typer.Option(None, "--model-provider"),
               model: Optional[str] = typer.Option(None, "--model"),
               run_id: Optional[str] = typer.Option(None, "--run-id")):
    _emit(_guard(_ena().train_eval, manifest_path=manifest, model_provider=model_provider,
                 model=model, run_id=run_id), title="eval")


@train_app.command("status")
def train_status(run_id: str = typer.Argument(...)):
    _emit(_guard(_ena().run_status, run_id))


@train_app.command("list")
def train_list():
    e = _ena()
    rows = e.list_runs()
    if _state.json:
        return _emit({"runs": rows})
    table = Table("run_id", "status", "backend", "base_model")
    for r in rows:
        table.add_row(r["run_id"], r["status"], r["backend"], r["base_model"])
    console.print(table)


@train_app.command("export")
def train_export(run_id: str = typer.Argument(...), out: Optional[str] = typer.Option(None, "--out")):
    _emit(_guard(_ena().export_run, run_id, out))


# ===========================================================================
# pool (collaborative training pools)
# ===========================================================================

pool_app = typer.Typer(help="Collaborative training pools.", no_args_is_help=True)
app.add_typer(pool_app, name="pool")


@pool_app.command("create")
def pool_create(base_model: str = typer.Option(..., "--base-model"),
                dataset: str = typer.Option(..., "--dataset"),
                method: str = typer.Option("lora", "--method"),
                name: Optional[str] = typer.Option(None, "--name"),
                num_shards: int = typer.Option(4, "--num-shards"),
                eval_metric: Optional[str] = typer.Option(None, "--eval-metric"),
                eval_min_score: Optional[float] = typer.Option(None, "--eval-min-score"),
                model_id: Optional[str] = typer.Option(None, "--model-id",
                    help="canonical global model this pool contributes to"),
                auto_promote: bool = typer.Option(True, "--auto-promote/--no-auto-promote",
                    help="auto-aggregate + promote each round once its shards are "
                         "in (default: on). --no-auto-promote drives promotion "
                         "explicitly via `pool aggregate`.")):
    eval_gate = ({"metric": eval_metric, "min_score": eval_min_score}
                 if eval_min_score is not None else None)
    pool = _guard(_ena().pool.create, base_model, dataset, method=method, name=name,
                  num_shards=num_shards, eval_gate=eval_gate, model_id=model_id,
                  auto_promote=auto_promote)
    _emit(pool, title="pool created")


@pool_app.command("models")
def pool_models():
    """List canonical global models (each is one model, trained by many pools)."""
    _emit({"models": _guard(_ena().pool.list_models)}, title="models")


@pool_app.command("model")
def pool_model(model_id: str = typer.Argument(..., help="global model id")):
    """Show a global model's current promoted head (what servers serve)."""
    _emit(_guard(_ena().pool.get_global_model, model_id), title="model")


@pool_app.command("list")
def pool_list(status: Optional[str] = typer.Option(None, "--status")):
    e = _ena()
    rows = e.pool.list_pools(status=status)
    if _state.json:
        return _emit({"pools": rows})
    table = Table("pool_id", "name", "status", "base_model", "round")
    for r in rows:
        table.add_row(r["pool_id"], r["name"], r["status"], r["base_model"], str(r["round"]))
    console.print(table)


@pool_app.command("status")
def pool_status(pool_id: str = typer.Argument(..., help="pool id")):
    _emit(_guard(_ena().pool.status, pool_id))


@pool_app.command("leaderboard")
def pool_leaderboard(pool_id: str = typer.Argument(..., help="pool id")):
    _emit({"leaderboard": _guard(_ena().pool.leaderboard, pool_id)})


@pool_app.command("fund-quote")
def pool_fund_quote(pool_id: str = typer.Argument(..., help="pool id"),
                    reward_anm: float = typer.Option(..., "--reward-anm"),
                    requester: Optional[str] = typer.Option(None, "--requester")):
    _emit(_guard(_ena().pool.fund_quote, pool_id, reward_anm, requester=requester),
          title="fund quote")


@pool_app.command("fund-confirm")
def pool_fund_confirm(pool_id: str = typer.Argument(..., help="pool id"),
                      txid: str = typer.Option(..., "--txid"),
                      requester: Optional[str] = typer.Option(None, "--requester")):
    _emit(_guard(_ena().pool.fund_confirm, pool_id, txid, requester=requester),
          title="fund confirm")


@pool_app.command("claim")
def pool_claim(pool_id: str = typer.Argument(..., help="pool id"),
               worker_id: str = typer.Option(..., "--worker-id")):
    _emit({"claimed": _guard(_ena().pool.claim_shard, pool_id, worker_id)}, title="claim")


@pool_app.command("submit")
def pool_submit(pool_id: str = typer.Argument(..., help="pool id"),
                shard_id: str = typer.Argument(..., help="shard id"),
                worker_id: Optional[str] = typer.Option(None, "--worker-id"),
                run_id: Optional[str] = typer.Option(None, "--run-id"),
                checkpoint: Optional[str] = typer.Option(None, "--checkpoint"),
                metrics: Optional[str] = typer.Option(None, "--metrics"),
                miner_address: Optional[str] = typer.Option(None, "--miner-address")):
    try:
        metrics_obj = _json.loads(metrics) if metrics else {}
    except ValueError as exc:
        console.print(f"[red]--metrics must be valid JSON: {exc}[/red]")
        raise typer.Exit(1)
    _emit(_guard(_ena().pool.submit_shard, pool_id, shard_id, worker_id=worker_id,
                 run_id=run_id, checkpoint_path=checkpoint, metrics=metrics_obj,
                 miner_address=miner_address), title="submitted")


@pool_app.command("aggregate")
def pool_aggregate(pool_id: str = typer.Argument(..., help="pool id"),
                   eval_score: Optional[float] = typer.Option(None, "--eval-score"),
                   min_submitted: Optional[int] = typer.Option(None, "--min-submitted")):
    _emit(_guard(_ena().pool.aggregate, pool_id, eval_score=eval_score,
                 min_submitted=min_submitted), title="aggregate")


@pool_app.command("payout")
def pool_payout(pool_id: str = typer.Argument(..., help="pool id"),
                round: Optional[int] = typer.Option(None, "--round")):
    _emit(_guard(_ena().pool.payout, pool_id, round=round), title="payout")


@pool_app.command("train-loop")
def pool_train_loop(pool_id: str = typer.Argument(..., help="pool id"),
                    worker_id: str = typer.Option(..., "--worker-id"),
                    address: Optional[str] = typer.Option(None, "--address",
                        help="ANM payout address for training rewards"),
                    backend: Optional[str] = typer.Option(None, "--backend"),
                    endpoint: Optional[str] = typer.Option(None, "--endpoint",
                        help="remote pool coordinator base URL (e.g. "
                             "https://pool.animica.org/api/ena). Omit for a "
                             "local coordinator.",
                        envvar="ANIMICA_ENA_ENDPOINT"),
                    once: bool = typer.Option(False, "--once",
                        help="train a single shard then exit"),
                    poll: float = typer.Option(10.0, "--poll",
                        help="seconds to wait when no shard is available")):
    """Claim → train → submit pool shards in a loop (GPU trainers).

    With --endpoint the pool lives on a remote coordinator: shards are claimed +
    downloaded over HTTP, trained locally, and the checkpoint uploaded back.
    """
    import time as _time
    from animica.ena.errors import ENAError
    from animica.ena.remote import RemoteError
    e = _ena()
    while True:
        # Transient errors (coordinator hiccup, network) must not kill the loop —
        # the supervisor would just respawn it and flood logs. Back off instead.
        try:
            res = e.train_shard_once(pool_id, worker_id, address=address,
                                     backend=backend, endpoint=endpoint)
        except (ENAError, RemoteError) as exc:
            if once:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(1)
            console.print(f"[yellow]train-loop: {exc}; retrying in {poll:.0f}s[/yellow]")
            _time.sleep(max(1.0, poll))
            continue
        if res is None:
            if once:
                _emit({"status": "no_shard"}, title="train-loop")
                return
            console.print("[dim]no shard available; waiting…[/dim]")
            _time.sleep(max(1.0, poll))
            continue
        _emit(res, title="train-loop")
        if once:
            return


@pool_app.command("serve")
def pool_serve(pool_id: str = typer.Argument(..., help="pool id"),
               worker_id: str = typer.Option(..., "--worker-id",
                   help="this server's id (credited for served tokens)"),
               host: str = typer.Option("127.0.0.1", "--host"),
               port: int = typer.Option(8799, "--port"),
               address: Optional[str] = typer.Option(None, "--address",
                   help="ANM payout address for served-token rewards"),
               endpoint: Optional[str] = typer.Option(None, "--endpoint",
                   help="remote pool coordinator base URL; the promoted "
                        "checkpoint is fetched + cached locally to serve.",
                   envvar="ANIMICA_ENA_ENDPOINT"),
               poll: float = typer.Option(20.0, "--poll",
                   help="seconds to wait for the first promoted checkpoint "
                        "before retrying (serve-while-train).")):
    """Serve the pool's promoted checkpoint (OpenAI-compatible; serve-while-train).

    A pool has no checkpoint to serve until its first round is aggregated, so
    this waits for one to be promoted instead of erroring — and keeps the process
    alive (no supervisor respawn flood) until serving can start.
    """
    import time as _time
    from animica.ena.errors import ENAError
    from animica.ena.remote import RemoteError
    e = _ena()
    announced = False
    while True:
        try:
            e.serve_model(pool_id, worker_id=worker_id, host=host, port=port,
                          address=address, endpoint=endpoint)
            return  # serve_model blocks; returns only on shutdown
        except (ENAError, RemoteError) as exc:
            msg = str(exc).lower()
            if "no promoted checkpoint" in msg or "not ready" in msg:
                if not announced:
                    console.print(f"[yellow]serve: pool {pool_id} has no promoted "
                                  f"checkpoint yet — waiting for the first round to "
                                  f"aggregate…[/yellow]")
                    announced = True
                _time.sleep(max(1.0, poll))
                continue
            render = exc.render() if isinstance(exc, ENAError) else str(exc)
            console.print(f"[red]{render}[/red]")
            raise typer.Exit(1)


# ===========================================================================
# memory / config / serve / scrape
# ===========================================================================

tools_app = typer.Typer(
    help="Dynamic tool registry: propose → review → approve. Proposed tools are "
         "NEVER active until an operator approves them with an admin token.",
    no_args_is_help=True)
app.add_typer(tools_app, name="tools")


@tools_app.command("list")
def tools_list(status: Optional[str] = typer.Option(None, "--status",
                   help="proposed | approved | rejected")):
    _emit({"tools": _ena().tools.list(status=status)}, title="tools")


@tools_app.command("propose")
def tools_propose(name: str = typer.Argument(..., help="tool name [a-z0-9_]"),
                  handler_file: str = typer.Option(..., "--handler-file",
                      help="Python file defining run(args: dict) -> str"),
                  description: str = typer.Option("", "--description"),
                  proposer: Optional[str] = typer.Option(None, "--proposer")):
    """Propose a new tool. It enters the queue as 'proposed' — NOT active."""
    from pathlib import Path as _P
    code = _P(handler_file).read_text(encoding="utf-8")
    rec = _guard(_ena().tools.propose, name, description, {}, code, proposer=proposer)
    if (rec.get("risk") or {}).get("flagged"):
        console.print(f"[yellow]⚠ AST scan flagged this handler: "
                      f"{', '.join(rec['risk']['reasons'])}[/yellow]")
    _emit(rec, title="proposed (pending review)")


@tools_app.command("approve")
def tools_approve(name: str = typer.Argument(...),
                  token: str = typer.Option(..., "--token",
                      envvar="ANIMICA_ENA_ADMIN_TOKEN",
                      help="admin token (must match the coordinator's)"),
                  approver: str = typer.Option("operator", "--approver")):
    """Approve a proposal — makes the tool active. Requires the admin token."""
    try:
        _emit(_ena().tools.approve(name, approver=approver, admin_token=token),
              title="approved")
    except (PermissionError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)


@tools_app.command("reject")
def tools_reject(name: str = typer.Argument(...),
                 reason: str = typer.Option("", "--reason"),
                 token: str = typer.Option(..., "--token",
                     envvar="ANIMICA_ENA_ADMIN_TOKEN")):
    try:
        _emit(_ena().tools.reject(name, reason=reason, admin_token=token),
              title="rejected")
    except (PermissionError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)


mem_app = typer.Typer(help="Agent memory.", no_args_is_help=True)
app.add_typer(mem_app, name="memory")


@mem_app.command("add")
def memory_add(text: str = typer.Argument(...), source: Optional[str] = typer.Option(None, "--source")):
    _emit(_guard(_ena().memory_add, text, source), title="memory")


@mem_app.command("query")
def memory_query(query: str = typer.Argument(...), limit: int = typer.Option(10, "--limit")):
    _emit({"results": _guard(_ena().memory_query, query, limit)})


config_app = typer.Typer(help="ENA configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")


@config_app.command("init")
def config_init(out: Optional[str] = typer.Option(None, "--out"),
                force: bool = typer.Option(False, "--force")):
    from animica.ena.config import init_config
    path = _guard(init_config, out, force)
    console.print(f"[green]wrote[/green] {path}")


@config_app.command("show")
def config_show():
    e = _ena()
    cfg = e.cfg.to_dict()
    cfg["sources"] = e.cfg.source_paths
    _emit(cfg)


@app.command("scrape")
def scrape(url: str = typer.Argument(...), out: str = typer.Option(..., "--out")):
    """Fetch a URL into a normalized raw.jsonl (single-page; honors job verify)."""
    e = _ena()
    job = e.jobs.create("scrape", {"url": url})
    job = _guard(e.jobs.run, job["job_id"])
    art = next((a for a in job.get("artifacts", []) if a["name"] == "raw.jsonl"), None)
    if art:
        import shutil
        shutil.copyfile(art["path"], out)
    _emit({"url": url, "out": out, "result": job.get("result")}, title="scrape")


@app.command("stats")
def stats():
    """Aggregate ENA progress stats (jobs, runs, contributors, models)."""
    e = _ena()
    s = e.stats()
    if _state.json:
        return _emit(s)
    console.print(f"[bold cyan]ENA progress[/bold cyan]")
    console.print(f"  jobs: {s['jobs_total']} total, {s['jobs_verified']} verified")
    console.print(f"  receipts: {s['receipts_total']}  |  training runs: {s['training_runs_total']}")
    console.print(f"  contributors: {s['contributors']}  |  models: {s['distinct_models']}")
    console.print(f"  indexes: {s['indexes_total']}  |  datasets: {s['datasets_total']}")


# -- worker -----------------------------------------------------------------
worker_app = typer.Typer(help="Contribute compute: claim + run useful-work and earn.",
                         no_args_is_help=True)
app.add_typer(worker_app, name="worker")


@worker_app.command("start")
def worker_start(worker_id: str = typer.Option(..., "--worker-id"),
                 endpoint: Optional[str] = typer.Option(None, "--endpoint",
                     envvar="ANIMICA_ENA_ENDPOINT",
                     help="Remote `ena serve` coordinator URL (omit for local store)"),
                 types: Optional[str] = typer.Option(None, "--types",
                     help="Comma-separated job types to accept (default: all)"),
                 max_jobs: Optional[int] = typer.Option(None, "--max-jobs"),
                 poll: float = typer.Option(3.0, "--poll", help="Idle poll seconds")):
    tl = [t.strip() for t in types.split(",")] if types else None
    e = None if endpoint else _ena()
    from animica.ena.worker import ENAWorker
    w = ENAWorker(worker_id=worker_id, ena=e, endpoint=endpoint, types=tl, poll_interval=poll)
    console.print(f"[green]ENA worker[/green] {worker_id} started "
                  f"({'remote '+endpoint if endpoint else 'local'}). Ctrl-C to stop.")

    def _on(event, data):
        if event == "completed":
            console.print(f"  ✓ {data['job_id']}  (earned≈{data['earnings_anm']} ANM)")
        elif event == "error":
            console.print(f"  [red]✗ {data['error']}[/red]")

    stats = _guard(w.run_forever, max_jobs, _on)
    console.print(f"\ncompleted={stats.jobs_completed} failed={stats.jobs_failed} "
                  f"earnings≈{stats.earnings_anm} ANM")


@worker_app.command("status")
def worker_status(state_path: Optional[str] = typer.Option(None, "--state-path")):
    from pathlib import Path
    p = Path(state_path) if state_path else Path("~/.animica/ena/worker-state.json").expanduser()
    if not p.is_file():
        console.print("[yellow]no worker state recorded[/yellow]")
        raise typer.Exit(1)
    _emit(_json.loads(p.read_text(encoding="utf-8")))


@app.command("serve")
def serve(host: str = typer.Option("127.0.0.1", "--host"),
          port: int = typer.Option(8787, "--port")):
    e = _ena()
    _guard(e.serve, host, port)


if __name__ == "__main__":  # pragma: no cover
    app()
