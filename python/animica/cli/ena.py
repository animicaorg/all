from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, List, Optional

import typer
import uvicorn
import yaml
from rich.console import Console
from rich.table import Table

from animica.ena.agent import AgentRunner
from animica.ena.config import load_ena_config, save_default_config
from animica.ena.datasets import DatasetManager
from animica.ena.ingest import Fetcher, export_jsonl, extract_local_path, load_seed_file, records_from_fetch
from animica.ena.jobs import JobManager, WorkerEngine
from animica.ena.models import AutonomyLevel, JobSpec, JobStatus, JobType, TaskSpec
from animica.ena.operator import EnaOperator
from animica.ena.providers import create_embedding_provider, create_model_provider
from animica.ena.retrieval import IndexManager
from animica.ena.service import create_app
from animica.ena.store import EnaStore
from animica.ena.training import TrainingManager

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

console = Console()
app = typer.Typer(help="ENA model runtime, retrieval, useful-work jobs, and training orchestration.")

agent_app = typer.Typer(help="Explicit agent runtime commands.")
train_app = typer.Typer(help="Training prepare/run/eval/status/export commands.")
eval_app = typer.Typer(help="Evaluation commands.")
jobs_app = typer.Typer(help="Useful-work job commands.")
worker_app = typer.Typer(help="Useful-work worker commands.")
datasets_app = typer.Typer(help="Dataset lifecycle commands.")
memory_app = typer.Typer(help="Structured memory commands.")
config_app = typer.Typer(help="ENA configuration commands.")
prove_app = typer.Typer(help="Verification and proof commands.")
models_app = typer.Typer(help="Model provider commands.")
embeddings_app = typer.Typer(help="Embedding provider commands.")
index_app = typer.Typer(help="Index build/rebuild/list commands.", invoke_without_command=True)
scrape_app = typer.Typer(help="Fetch, batch scrape, and constrained crawl workflows.")
ingest_app = typer.Typer(help="Ingest local files and directories.")
extract_app = typer.Typer(help="Structured extraction commands.")
collect_app = typer.Typer(help="Dataset collection and build commands.")
artifacts_app = typer.Typer(help="Artifact inspection and verification.")
runs_app = typer.Typer(help="Agent run inspection commands.")
credits_app = typer.Typer(help="AICF credit inspection commands.")
mining_app = typer.Typer(help="ENA useful-work mining integration commands.")

app.add_typer(agent_app, name="agent")
app.add_typer(train_app, name="train")
app.add_typer(eval_app, name="eval")
app.add_typer(jobs_app, name="jobs")
app.add_typer(worker_app, name="worker")
app.add_typer(datasets_app, name="datasets")
app.add_typer(memory_app, name="memory")
app.add_typer(config_app, name="config")
app.add_typer(prove_app, name="prove")
app.add_typer(models_app, name="models")
app.add_typer(embeddings_app, name="embeddings")
app.add_typer(index_app, name="index")
app.add_typer(scrape_app, name="scrape")
app.add_typer(ingest_app, name="ingest")
app.add_typer(extract_app, name="extract")
app.add_typer(collect_app, name="collect")
app.add_typer(artifacts_app, name="artifacts")
app.add_typer(runs_app, name="runs")
app.add_typer(credits_app, name="credits")
app.add_typer(mining_app, name="mining")

try:
    from . import ena_artifact

    app.add_typer(ena_artifact.app, name="artifact", help="Artifact verification and inspection")
except Exception:
    pass

try:
    from . import ena_upgrade

    app.add_typer(ena_upgrade.app, name="upgrade", help="Legacy ENA model upgrade commands")
except Exception:
    pass


class _State:
    config_path: Optional[Path] = None
    json_output: bool = False
    verbose: bool = False


_state = _State()


@app.callback()
def ena_callback(
    config_path: Optional[Path] = typer.Option(None, "--config", help="Path to ENA config file"),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON output"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    _state.config_path = config_path
    _state.json_output = json_output
    _state.verbose = verbose
    if config_path:
        os.environ["ANIMICA_ENA_CONFIG"] = str(config_path)


def _runtime() -> tuple[Any, EnaStore, AgentRunner, IndexManager, DatasetManager, JobManager, WorkerEngine, TrainingManager]:
    config = load_ena_config(explicit_path=_state.config_path)
    store = EnaStore(config)
    runner = AgentRunner(config=config, store=store)
    index = IndexManager(store, config)
    datasets = DatasetManager(store, config)
    jobs = JobManager(store, config)
    worker = WorkerEngine(store, config)
    training = TrainingManager(store, config)
    return config, store, runner, index, datasets, jobs, worker, training


def _operator_runtime() -> tuple[Any, EnaStore, EnaOperator]:
    config = load_ena_config(explicit_path=_state.config_path)
    store = EnaStore(config)
    operator = EnaOperator(store=store, config=config)
    return config, store, operator


def _emit(data: Any, *, json_output: Optional[bool] = None) -> None:
    if json_output is None:
        json_output = _state.json_output
    if json_output:
        console.print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        if isinstance(data, str):
            console.print(data)
        else:
            console.print(json.dumps(data, indent=2, ensure_ascii=False))


def _emit_hits(hits: List[Any]) -> None:
    if _state.json_output:
        _emit([hit.model_dump(mode="json") for hit in hits])
        return
    table = Table(title="Search Results")
    table.add_column("Score", style="cyan")
    table.add_column("Lexical", style="magenta")
    table.add_column("Semantic", style="yellow")
    table.add_column("Source", style="green")
    table.add_column("Excerpt", style="white")
    for hit in hits:
        table.add_row(
            f"{hit.score:.2f}",
            f"{hit.lexical_score:.2f}",
            f"{hit.semantic_score:.2f}",
            hit.source,
            hit.excerpt[:140],
        )
    console.print(table)


def _task_spec_from_file(path: Path) -> TaskSpec:
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) if path.suffix in {".yaml", ".yml"} else json.loads(raw)
    return TaskSpec.model_validate(data)


def _job_type_list(csv_text: str) -> List[JobType]:
    return [JobType(item.strip()) for item in csv_text.split(",") if item.strip()]


def _launcher_dict(command: Optional[str]) -> dict:
    if not command:
        return {}
    return {"command": shlex.split(command)}


@models_app.command("list")
def models_list(
    provider: Optional[str] = typer.Option(None, "--provider", help="Configured model provider name"),
    remote: bool = typer.Option(False, "--remote", help="Query the provider endpoint for available models"),
) -> None:
    config, store, runner, index, datasets, jobs, worker, training = _runtime()
    if provider:
        provider_names = [provider]
    else:
        provider_names = sorted(config.model_providers.keys())
    items = []
    for provider_name in provider_names:
        model = create_model_provider(config, provider_name=provider_name)
        entry: dict[str, Any] = {
            "provider_name": provider_name,
            "provider": model.config.provider,
            "transport": model.config.transport,
            "model": model.config.model,
            "base_url": model.config.base_url or model.config.endpoint,
        }
        if remote:
            try:
                entry["remote_models"] = model.list_models()
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)
        items.append(entry)
    _emit(items)


@models_app.command("test")
def models_test(
    provider: Optional[str] = typer.Option(None, "--provider", help="Configured model provider name"),
    model_name: Optional[str] = typer.Option(None, "--model", help="Override model name"),
) -> None:
    config, store, runner, index, datasets, jobs, worker, training = _runtime()
    model = create_model_provider(config, provider_name=provider)
    if model_name:
        model.config = model.config.model_copy(update={"model": model_name})
    _emit(model.test())


@embeddings_app.command("test")
def embeddings_test(
    provider: Optional[str] = typer.Option(None, "--provider", help="Configured embedding provider name"),
) -> None:
    config, store, runner, index, datasets, jobs, worker, training = _runtime()
    provider_name = provider or config.default_embedding_provider
    embedding = create_embedding_provider(config, provider_name=provider_name)
    _emit(embedding.test())


@index_app.callback(invoke_without_command=True)
def index_callback(
    ctx: typer.Context,
) -> None:
    if ctx.invoked_subcommand is None:
        _emit({"indexes": [item.model_dump(mode="json") for item in _runtime()[1].list_indexes()]})


def _build_index(path: Path, *, name: Optional[str], reset: bool, embedding_provider: Optional[str]) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    if path.suffix == ".jsonl":
        result = indexer.index_jsonl_records(path, index_name=name, reset=reset, embedding_provider_name=embedding_provider)
    else:
        result = indexer.index_path(path, index_name=name, reset=reset, embedding_provider_name=embedding_provider)
    _emit(result)


@index_app.command("build")
def index_build(
    path: Path = typer.Argument(..., help="Path or JSONL file to index"),
    name: Optional[str] = typer.Option(None, "--name", help="Index name"),
    embedding_provider: Optional[str] = typer.Option(None, "--embedding-provider", help="Embedding provider name"),
) -> None:
    _build_index(path, name=name, reset=False, embedding_provider=embedding_provider)


@index_app.command("rebuild")
def index_rebuild(
    path: Path = typer.Argument(..., help="Path or JSONL file to re-index"),
    name: Optional[str] = typer.Option(None, "--name", help="Index name"),
    embedding_provider: Optional[str] = typer.Option(None, "--embedding-provider", help="Embedding provider name"),
) -> None:
    _build_index(path, name=name, reset=True, embedding_provider=embedding_provider)


@index_app.command("list")
def index_list() -> None:
    _emit({"indexes": [item.model_dump(mode="json") for item in _runtime()[1].list_indexes()]})


@index_app.command("stats")
def index_stats(index_name: str = typer.Argument(..., help="Index name")) -> None:
    config, store, operator = _operator_runtime()
    _emit(operator.index.stats(index_name))


@app.command("chat")
def chat(
    repo: Optional[Path] = typer.Option(None, "--repo", help="Repository or folder to index for context"),
    url: List[str] = typer.Option([], "--url", help="URL to fetch into context"),
    autonomy: AutonomyLevel = typer.Option(AutonomyLevel.WORKSPACE, "--autonomy", help="Autonomy level"),
    model_provider: Optional[str] = typer.Option(None, "--model-provider", help="Configured model provider name"),
    model_name: Optional[str] = typer.Option(None, "--model", help="Override model name"),
) -> None:
    config, store, runner, index, datasets, jobs, worker, training = _runtime()
    context_paths = [str(repo.resolve())] if repo else []
    console.print("ENA chat session. Type `exit` to leave.")
    while True:
        prompt = input("ena> ").strip()
        if prompt.lower() in {"exit", "quit"}:
            break
        if not prompt:
            continue
        result = runner.ask(
            prompt,
            context_paths=context_paths,
            urls=url,
            autonomy=autonomy,
            model_provider=model_provider,
            model=model_name,
        )
        if _state.json_output:
            _emit(result)
        else:
            console.print(result["answer"])
            for citation in result.get("citations", [])[:5]:
                console.print(f"[dim]- {citation['source']}[/dim]")


@app.command("ask")
def ask(
    question: str = typer.Argument(..., help="Research or repo question"),
    context: List[Path] = typer.Option([], "--context", help="Context path to search/index"),
    url: List[str] = typer.Option([], "--url", help="URL to fetch into context"),
    save_as: Optional[Path] = typer.Option(None, "--save-as", help="Save result artifact path"),
    autonomy: AutonomyLevel = typer.Option(AutonomyLevel.WORKSPACE, "--autonomy", help="Autonomy level"),
    model_provider: Optional[str] = typer.Option(None, "--model-provider", help="Configured model provider name"),
    model_name: Optional[str] = typer.Option(None, "--model", help="Override model name"),
) -> None:
    config, store, runner, index, datasets, jobs, worker, training = _runtime()
    result = runner.run(
        TaskSpec(
            task=question,
            context_paths=[str(item.resolve()) for item in context],
            urls=url,
            autonomy=autonomy,
            save_as=str(save_as) if save_as else None,
            output_format="json" if _state.json_output else "text",
            model_provider=model_provider,
            model=model_name,
        )
    )
    if _state.json_output:
        _emit(result)
    else:
        console.print(result["answer"])
        if result.get("citations"):
            console.print("")
            for citation in result["citations"][:6]:
                console.print(f"[dim]- {citation['source']}[/dim]")


@app.command("plan")
def plan(
    task: str = typer.Argument(..., help="Task description"),
    context: List[Path] = typer.Option([], "--context", help="Context path"),
    url: List[str] = typer.Option([], "--url", help="Context URL"),
    autonomy: AutonomyLevel = typer.Option(AutonomyLevel.WORKSPACE, "--autonomy", help="Autonomy level"),
    model_provider: Optional[str] = typer.Option(None, "--model-provider", help="Configured model provider name"),
    model_name: Optional[str] = typer.Option(None, "--model", help="Override model name"),
) -> None:
    config, store, runner, index, datasets, jobs, worker, training = _runtime()
    spec = TaskSpec(
        task=task,
        context_paths=[str(item.resolve()) for item in context],
        urls=url,
        autonomy=autonomy,
        model_provider=model_provider,
        model=model_name,
    )
    _emit({"task": task, "plan": runner.plan(spec)})


@app.command("run")
def run_task(
    task_or_file: str = typer.Argument(..., help="Task text or YAML/JSON task file"),
    context: List[Path] = typer.Option([], "--context", help="Context path"),
    url: List[str] = typer.Option([], "--url", help="Context URL"),
    save_as: Optional[Path] = typer.Option(None, "--save-as", help="Save output path"),
    autonomy: AutonomyLevel = typer.Option(AutonomyLevel.WORKSPACE, "--autonomy", help="Autonomy level"),
    model_provider: Optional[str] = typer.Option(None, "--model-provider", help="Configured model provider name"),
    model_name: Optional[str] = typer.Option(None, "--model", help="Override model name"),
) -> None:
    config, store, runner, index, datasets, jobs, worker, training = _runtime()
    maybe_path = Path(task_or_file)
    if maybe_path.exists():
        spec = _task_spec_from_file(maybe_path)
        if model_provider and not spec.model_provider:
            spec.model_provider = model_provider
        if model_name and not spec.model:
            spec.model = model_name
    else:
        spec = TaskSpec(
            task=task_or_file,
            context_paths=[str(item.resolve()) for item in context],
            urls=url,
            autonomy=autonomy,
            save_as=str(save_as) if save_as else None,
            output_format="json" if _state.json_output else "text",
            model_provider=model_provider,
            model=model_name,
        )
    _emit(runner.run(spec))


@agent_app.command("run")
def agent_run(
    task_or_file: str = typer.Argument(..., help="Task text or task file"),
    model_provider: Optional[str] = typer.Option(None, "--model-provider", help="Configured model provider name"),
    model_name: Optional[str] = typer.Option(None, "--model", help="Override model name"),
) -> None:
    run_task(task_or_file, model_provider=model_provider, model_name=model_name)


@agent_app.command("plan")
def agent_plan(
    task: str = typer.Argument(..., help="Task description"),
    model_provider: Optional[str] = typer.Option(None, "--model-provider", help="Configured model provider name"),
    model_name: Optional[str] = typer.Option(None, "--model", help="Override model name"),
) -> None:
    plan(task, model_provider=model_provider, model_name=model_name)


@app.command("fetch")
def fetch(
    url: str = typer.Argument(..., help="URL to fetch"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write normalized JSONL records"),
) -> None:
    config, store, runner, index, datasets, jobs, worker, training = _runtime()
    result = runner.tools.fetch(url)
    if out:
        Path(out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    _emit(result)


@scrape_app.command("url")
def scrape_url(
    url: str = typer.Argument(..., help="URL to fetch or crawl"),
    depth: int = typer.Option(0, "--depth", help="Crawl depth. Use 0 for single-page fetch."),
    max_requests: int = typer.Option(25, "--max-requests", help="Maximum requests"),
    include_sitemap: bool = typer.Option(False, "--include-sitemap", help="Seed crawl URLs from sitemap.xml when crawling"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write JSONL output"),
    index_after: bool = typer.Option(False, "--index", help="Index the scraped output after writing it"),
    index_name: Optional[str] = typer.Option(None, "--index-name", help="Optional index name"),
    embedding_provider: Optional[str] = typer.Option(None, "--embedding-provider", help="Embedding provider name"),
) -> None:
    config, store, operator = _operator_runtime()
    _emit(
        operator.scrape_url(
            url,
            depth=depth,
            max_requests=max_requests,
            include_sitemap=include_sitemap,
            out=out,
            index_after=index_after,
            index_name=index_name,
            embedding_provider=embedding_provider,
        )
    )


@scrape_app.command("batch")
def scrape_batch(
    seed_file: Path = typer.Argument(..., help="File with one URL per line"),
    depth: int = typer.Option(0, "--depth", help="Crawl depth for each seed"),
    max_requests: int = typer.Option(25, "--max-requests", help="Maximum requests"),
    include_sitemap: bool = typer.Option(False, "--include-sitemap", help="Seed crawl URLs from sitemap.xml when crawling"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write JSONL output"),
    index_after: bool = typer.Option(False, "--index", help="Index the scraped output after writing it"),
    index_name: Optional[str] = typer.Option(None, "--index-name", help="Optional index name"),
    embedding_provider: Optional[str] = typer.Option(None, "--embedding-provider", help="Embedding provider name"),
) -> None:
    config, store, operator = _operator_runtime()
    _emit(
        operator.scrape_batch(
            seed_file,
            depth=depth,
            max_requests=max_requests,
            include_sitemap=include_sitemap,
            out=out,
            index_after=index_after,
            index_name=index_name,
            embedding_provider=embedding_provider,
        )
    )


@scrape_app.command("crawl")
def scrape_crawl(
    root_url: str = typer.Argument(..., help="Root URL to crawl"),
    depth: int = typer.Option(2, "--depth", help="Maximum crawl depth"),
    max_requests: int = typer.Option(50, "--max-requests", help="Maximum requests"),
    include_sitemap: bool = typer.Option(False, "--include-sitemap", help="Seed crawl URLs from sitemap.xml"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write JSONL output"),
    index_after: bool = typer.Option(False, "--index", help="Index the crawl output after writing it"),
    index_name: Optional[str] = typer.Option(None, "--index-name", help="Optional index name"),
    embedding_provider: Optional[str] = typer.Option(None, "--embedding-provider", help="Embedding provider name"),
) -> None:
    config, store, operator = _operator_runtime()
    _emit(
        operator.scrape_crawl(
            root_url,
            depth=depth,
            max_requests=max_requests,
            include_sitemap=include_sitemap,
            out=out,
            index_after=index_after,
            index_name=index_name,
            embedding_provider=embedding_provider,
        )
    )


@extract_app.command("records")
def extract_records(
    source: List[str] = typer.Argument(..., help="Local path(s) or URL(s) to extract"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write extracted JSONL"),
) -> None:
    config, store, operator = _operator_runtime()
    records = operator._records_from_sources(source)
    result = operator._persist_records(
        kind="extract_records",
        records=records,
        out=out or (Path(config.storage.datasets_dir) / f"{Path(source[0]).stem}.extract.jsonl"),
        metadata={"sources": list(source)},
    )
    _emit(result)


@extract_app.command("schema")
def extract_schema(
    source: List[str] = typer.Argument(..., help="Local path(s) or URL(s) to extract from"),
    schema_text: Optional[str] = typer.Option(None, "--schema", help="Inline JSON schema"),
    schema_file: Optional[Path] = typer.Option(None, "--schema-file", help="Path to JSON schema file"),
    instruction: str = typer.Option("Extract the requested fields from the provided source.", "--instruction", help="Extraction instruction"),
    model_provider: Optional[str] = typer.Option(None, "--model-provider", help="Configured model provider name"),
    model_name: Optional[str] = typer.Option(None, "--model", help="Override model name"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write extracted JSONL"),
) -> None:
    if not schema_text and not schema_file:
        raise typer.BadParameter("provide --schema or --schema-file")
    schema = json.loads(schema_text) if schema_text else json.loads(schema_file.read_text(encoding="utf-8"))
    config, store, operator = _operator_runtime()
    _emit(
        operator.extract_schema(
            source,
            schema=schema,
            instruction=instruction,
            model_provider=model_provider,
            model_name=model_name,
            out=out,
        )
    )


@app.command("search")
def search(
    query: str = typer.Argument(..., help="Search query"),
    index_name: Optional[str] = typer.Option(None, "--index", help="Index name"),
    limit: int = typer.Option(8, "--limit", help="Maximum hits"),
    semantic: bool = typer.Option(False, "--semantic", help="Use semantic-only ranking"),
    hybrid: bool = typer.Option(False, "--hybrid", help="Use hybrid keyword+semantic ranking"),
    keyword: bool = typer.Option(False, "--keyword", help="Use keyword-only ranking"),
    embedding_provider: Optional[str] = typer.Option(None, "--embedding-provider", help="Embedding provider name"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    if keyword:
        strategy = "keyword"
    elif semantic:
        strategy = "semantic"
    elif hybrid:
        strategy = "hybrid"
    else:
        strategy = "hybrid"
    hits = indexer.search(query, index_name=index_name, limit=limit, strategy=strategy, embedding_provider_name=embedding_provider)
    _emit_hits(hits)


@app.command("summarize")
def summarize(
    query: str = typer.Argument(..., help="Question or topic to summarize"),
    source: List[str] = typer.Option([], "--source", help="Local path(s) or URL(s) to summarize"),
    index_name: Optional[str] = typer.Option(None, "--index", help="Existing index name"),
    model_provider: Optional[str] = typer.Option(None, "--model-provider", help="Configured model provider name"),
    model_name: Optional[str] = typer.Option(None, "--model", help="Override model name"),
    embedding_provider: Optional[str] = typer.Option(None, "--embedding-provider", help="Embedding provider name"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write JSON output"),
) -> None:
    config, store, operator = _operator_runtime()
    _emit(
        operator.summarize(
            query,
            sources=source or None,
            index_name=index_name,
            model_provider=model_provider,
            model_name=model_name,
            embedding_provider=embedding_provider,
            out=out,
        )
    )


@jobs_app.command("create")
def jobs_create(
    spec_file: Optional[Path] = typer.Option(None, "--spec", help="JSON or YAML job spec"),
    job_type: Optional[JobType] = typer.Option(None, "--type", help="Job type"),
    source: List[str] = typer.Option([], "--source", help="Source URL or path"),
    path: Optional[str] = typer.Option(None, "--path", help="Input path"),
    dataset: Optional[str] = typer.Option(None, "--dataset", help="Input dataset"),
    query: Optional[str] = typer.Option(None, "--query", help="Query for summarize/index jobs"),
    labels: List[str] = typer.Option([], "--label", help="Labels for label/classify jobs"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    if spec_file:
        raw = spec_file.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) if spec_file.suffix in {".yaml", ".yml"} else json.loads(raw)
        spec = JobSpec.model_validate(data)
    else:
        if job_type is None:
            raise typer.BadParameter("--type is required when --spec is not used")
        spec = JobSpec(
            job_type=job_type,
            input_payload={key: value for key, value in {"path": path, "dataset": dataset, "query": query, "labels": labels}.items() if value},
            sources=source,
            allowed_actions=[job_type.value],
        )
    record = jobs.create(spec)
    _emit(record.model_dump(mode="json"))


@jobs_app.command("propose")
def jobs_propose(
    spec_file: Optional[Path] = typer.Option(None, "--spec", help="JSON or YAML job spec"),
    job_type: Optional[JobType] = typer.Option(None, "--type", help="Job type"),
    source: List[str] = typer.Option([], "--source", help="Source URL or path"),
    path: Optional[str] = typer.Option(None, "--path", help="Input path"),
    dataset: Optional[str] = typer.Option(None, "--dataset", help="Input dataset"),
    query: Optional[str] = typer.Option(None, "--query", help="Query for summarize/index jobs"),
) -> None:
    jobs_create(spec_file=spec_file, job_type=job_type, source=source, path=path, dataset=dataset, query=query, labels=[])


@jobs_app.command("list")
def jobs_list(status: Optional[JobStatus] = typer.Option(None, "--status", help="Filter by status")) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    records = jobs.list(status=status)
    if _state.json_output:
        _emit([record.model_dump(mode="json") for record in records])
        return
    table = Table(title="Useful-Work Jobs")
    table.add_column("Job")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Claimed By")
    table.add_column("Credits")
    for record in records:
        table.add_row(record.job_id, record.job_type.value, record.status.value, record.claimed_by or "-", str(record.reward.get("credits", 0)))
    console.print(table)


@jobs_app.command("show")
def jobs_show(job_id: str = typer.Argument(..., help="Job ID")) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    job = jobs.get(job_id)
    if job is None:
        raise typer.Exit(code=1)
    _emit({"job": job.model_dump(mode="json"), "events": store.list_job_events(job_id)})


@jobs_app.command("claim")
def jobs_claim(
    worker_id: str = typer.Option("local-worker", "--worker-id", help="Worker identity"),
    types: str = typer.Option("", "--types", help="Comma-separated job types"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    claimed = jobs.claim(worker_id, types=_job_type_list(types) if types else None)
    _emit(claimed.model_dump(mode="json") if claimed else {"claimed": None})


@jobs_app.command("run")
def jobs_run(
    job_id: Optional[str] = typer.Argument(None, help="Optional specific job ID"),
    worker_id: str = typer.Option("local-worker", "--worker-id", help="Worker identity"),
    types: str = typer.Option("", "--types", help="Comma-separated job types"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    if job_id:
        record = jobs.get(job_id)
        if record is None:
            raise typer.Exit(code=1)
        if record.status == JobStatus.PROPOSED:
            claimed = jobs.claim_job(job_id, worker_id)
            if claimed is not None:
                record = claimed
        result = worker.execute(record)
        _emit(result.model_dump(mode="json"))
        return
    records = worker.run_claimed(worker_id, types=_job_type_list(types) if types else None, limit=1)
    _emit([record.model_dump(mode="json") for record in records])


@jobs_app.command("submit")
def jobs_submit(
    job_id: str = typer.Argument(..., help="Job ID"),
    result_file: Path = typer.Argument(..., help="JSON result payload"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    record = jobs.get(job_id)
    if record is None:
        raise typer.Exit(code=1)
    payload = json.loads(result_file.read_text(encoding="utf-8"))
    submitted = jobs.submit(record, payload)
    _emit(submitted.model_dump(mode="json"))


@jobs_app.command("verify")
def jobs_verify(job_id: str = typer.Argument(..., help="Job ID")) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    record = jobs.get(job_id)
    if record is None:
        raise typer.Exit(code=1)
    verified = jobs.verify(record)
    _emit(verified.model_dump(mode="json"))


@jobs_app.command("receipt")
def jobs_receipt(job_id: str = typer.Argument(..., help="Job ID")) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    receipt = jobs.receipt(job_id)
    if receipt is None:
        raise typer.Exit(code=1)
    _emit(receipt.model_dump(mode="json"))


@jobs_app.command("export-onchain")
def jobs_export_onchain(job_id: str = typer.Argument(..., help="Job ID")) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    payload = jobs.export_onchain(job_id)
    if payload is None:
        raise typer.Exit(code=1)
    _emit(payload)


@jobs_app.command("worker")
def jobs_worker(
    claim: bool = typer.Option(True, "--claim/--no-claim", help="Claim work before running"),
    worker_id: str = typer.Option("local-worker", "--worker-id", help="Worker identity"),
    types: str = typer.Option("", "--types", help="Comma-separated job types"),
    limit: int = typer.Option(1, "--limit", help="Maximum jobs to execute"),
) -> None:
    worker_run(claim=claim, worker_id=worker_id, types=types, limit=limit)


@worker_app.command("run")
def worker_run(
    claim: bool = typer.Option(True, "--claim/--no-claim", help="Claim work before running"),
    worker_id: str = typer.Option("local-worker", "--worker-id", help="Worker identity"),
    types: str = typer.Option("", "--types", help="Comma-separated job types"),
    limit: int = typer.Option(1, "--limit", help="Maximum jobs to execute"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    if not claim:
        raise typer.BadParameter("worker currently expects --claim")
    records = worker.run_claimed(worker_id, types=_job_type_list(types) if types else None, limit=limit)
    _emit([record.model_dump(mode="json") for record in records])


@datasets_app.command("list")
def datasets_list() -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    records = datasets.list()
    if _state.json_output:
        _emit([record.model_dump(mode="json") for record in records])
        return
    table = Table(title="Datasets")
    table.add_column("Dataset")
    table.add_column("Kind")
    table.add_column("Rows")
    table.add_column("Path")
    for record in records:
        table.add_row(record.dataset_id, record.kind, str(record.row_count), record.path)
    console.print(table)


@datasets_app.command("ingest")
def datasets_ingest(
    path: Path = typer.Argument(..., help="JSONL dataset path"),
    kind: str = typer.Option("raw", "--kind", help="Dataset kind"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    record = datasets.register(path, kind=kind)
    _emit(record.model_dump(mode="json"))


@datasets_app.command("normalize")
def datasets_normalize(
    path: Path = typer.Argument(..., help="Input JSONL path"),
    out: Path = typer.Option(..., "--out", help="Output JSONL"),
    task_type: str = typer.Option("summarize", "--task-type", help="Training sample task type"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    _emit(datasets.normalize(path, out, task_type=task_type))


@datasets_app.command("dedupe")
def datasets_dedupe(
    path: Path = typer.Argument(..., help="Input JSONL path"),
    out: Path = typer.Option(..., "--out", help="Output JSONL"),
    near_distance: int = typer.Option(3, "--near-distance", help="Simhash distance threshold"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    _emit(datasets.dedupe(path, out, near_duplicate_distance=near_distance))


@datasets_app.command("shard")
def datasets_shard(
    path: Path = typer.Argument(..., help="Input JSONL path"),
    out_dir: Path = typer.Option(..., "--out-dir", help="Output directory"),
    rows_per_shard: int = typer.Option(500, "--rows-per-shard", help="Rows per shard"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    _emit(datasets.shard(path, out_dir, rows_per_shard=rows_per_shard))


@datasets_app.command("split")
def datasets_split(
    path: Path = typer.Argument(..., help="Input JSONL path"),
    out_dir: Path = typer.Option(..., "--out-dir", help="Output directory"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    _emit(datasets.split_dataset(path, out_dir))


@datasets_app.command("validate")
def datasets_validate(
    path: Path = typer.Argument(..., help="Input JSONL path"),
    schema_name: str = typer.Option("training_sample", "--schema", help="Schema name"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    result = datasets.validate(path, schema_name=schema_name)
    if not result["ok"]:
        _emit(result)
        raise typer.Exit(code=1)
    _emit(result)


@datasets_app.command("export")
def datasets_export(
    path: Path = typer.Argument(..., help="Input JSONL path"),
    out: Path = typer.Option(..., "--out", help="Output path"),
    format_name: str = typer.Option("jsonl", "--format", help="jsonl or parquet"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    _emit(datasets.export(path, out, format_name=format_name))


@ingest_app.command("file")
def ingest_file(
    path: Path = typer.Argument(..., help="Local file to ingest"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write JSONL output"),
    index_after: bool = typer.Option(False, "--index", help="Index the ingested output after writing it"),
    index_name: Optional[str] = typer.Option(None, "--index-name", help="Optional index name"),
    embedding_provider: Optional[str] = typer.Option(None, "--embedding-provider", help="Embedding provider name"),
) -> None:
    config, store, operator = _operator_runtime()
    _emit(
        operator.ingest_file(
            path,
            out=out,
            index_after=index_after,
            index_name=index_name,
            embedding_provider=embedding_provider,
        )
    )


@ingest_app.command("dir")
def ingest_dir(
    path: Path = typer.Argument(..., help="Directory to ingest"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write JSONL output"),
    index_after: bool = typer.Option(False, "--index", help="Index the ingested output after writing it"),
    index_name: Optional[str] = typer.Option(None, "--index-name", help="Optional index name"),
    embedding_provider: Optional[str] = typer.Option(None, "--embedding-provider", help="Embedding provider name"),
) -> None:
    config, store, operator = _operator_runtime()
    _emit(
        operator.ingest_dir(
            path,
            out=out,
            index_after=index_after,
            index_name=index_name,
            embedding_provider=embedding_provider,
        )
    )


@collect_app.command("build-dataset")
def collect_build_dataset(
    input_paths: List[Path] = typer.Argument(..., help="Input file(s) or JSONL source(s)"),
    raw_out: Path = typer.Option(..., "--raw-out", help="Combined raw JSONL output"),
    task_type: str = typer.Option("summarize", "--task-type", help="Training task type"),
    dedupe: bool = typer.Option(True, "--dedupe/--no-dedupe", help="Deduplicate normalized rows"),
    split: bool = typer.Option(False, "--split/--no-split", help="Create deterministic train/eval/test splits"),
    manifest_path: Optional[Path] = typer.Option(None, "--manifest", help="Write dataset build manifest"),
) -> None:
    config, store, operator = _operator_runtime()
    _emit(
        operator.build_dataset(
            input_paths,
            raw_out=raw_out,
            task_type=task_type,
            dedupe=dedupe,
            split=split,
            manifest_path=manifest_path,
        )
    )


@train_app.command("prepare")
def train_prepare(
    dataset: Path = typer.Option(..., "--dataset", help="Training dataset path"),
    out: Path = typer.Option(..., "--out", help="Training manifest path"),
    base_model: str = typer.Option(..., "--base-model", help="Base model path or identifier"),
    backend: str = typer.Option("command", "--backend", help="Training backend"),
    eval_dataset: Optional[Path] = typer.Option(None, "--eval-dataset", help="Optional eval dataset"),
    test_dataset: Optional[Path] = typer.Option(None, "--test-dataset", help="Optional test dataset"),
    auto_split: bool = typer.Option(False, "--auto-split", help="Create deterministic train/eval/test splits"),
    launcher_command: Optional[str] = typer.Option(None, "--launcher-command", help="External trainer command with {manifest} and {output_dir} placeholders"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    _emit(
        training.prepare(
            dataset,
            out_path=out,
            base_model=base_model,
            backend=backend,
            eval_dataset_path=eval_dataset,
            test_dataset_path=test_dataset,
            auto_split=auto_split,
            launcher=_launcher_dict(launcher_command),
        )
    )


@train_app.command("run")
def train_run(
    manifest: Path = typer.Option(..., "--manifest", help="Training manifest path"),
    backend: Optional[str] = typer.Option(None, "--backend", help="Override training backend"),
    command: Optional[str] = typer.Option(None, "--command", help="Override training command"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    record = training.run(manifest, backend=backend, command=shlex.split(command) if command else None)
    _emit(record.model_dump(mode="json"))


@train_app.command("eval")
def train_eval(
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Training run id"),
    manifest: Optional[Path] = typer.Option(None, "--manifest", help="Training manifest path"),
    dataset: Optional[Path] = typer.Option(None, "--dataset", help="Dataset path override"),
    model_provider: Optional[str] = typer.Option(None, "--model-provider", help="Configured model provider name"),
    model_name: Optional[str] = typer.Option(None, "--model", help="Override model name"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    _emit(
        training.eval(
            run_id=run_id,
            manifest_path=manifest,
            dataset_path=dataset,
            model_provider=model_provider,
            model=model_name,
        )
    )


@train_app.command("status")
def train_status(run_id: str = typer.Argument(..., help="Training run id")) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    record = training.status(run_id)
    if record is None:
        raise typer.Exit(code=1)
    _emit(record.model_dump(mode="json"))


@train_app.command("list")
def train_list(limit: int = typer.Option(100, "--limit", help="Maximum runs")) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    _emit([record.model_dump(mode="json") for record in training.list_runs(limit=limit)])


@train_app.command("export")
def train_export(
    run_id: str = typer.Argument(..., help="Training run id"),
    out: Path = typer.Option(..., "--out", help="Export path"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    _emit(training.export(run_id, out))


@train_app.command("resume")
def train_resume(
    run_id: str = typer.Argument(..., help="Training run id to resume"),
    backend: Optional[str] = typer.Option(None, "--backend", help="Override training backend"),
    command: Optional[str] = typer.Option(None, "--command", help="Override training command"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    record = training.resume(run_id, backend=backend, command=shlex.split(command) if command else None)
    _emit(record.model_dump(mode="json"))


@eval_app.command("run")
def eval_run(
    dataset: Path = typer.Option(..., "--dataset", help="Dataset path"),
    suite_name: str = typer.Option("dataset_stats", "--suite-name", help="Eval suite name"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    spec = JobSpec(job_type=JobType.EVAL, input_payload={"dataset": str(dataset), "suite_name": suite_name})
    record = jobs.create(spec)
    verified = worker.execute(record)
    _emit(verified.model_dump(mode="json"))


@memory_app.command("add")
def memory_add(
    kind: str = typer.Option("note", "--kind", help="Memory kind"),
    content: str = typer.Argument(..., help="Memory content"),
    source: Optional[str] = typer.Option(None, "--source", help="Source reference"),
    confidence: float = typer.Option(0.7, "--confidence", help="Confidence score"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    _emit({"memory_id": store.add_memory(kind=kind, content=content, source=source, confidence=confidence)})


@memory_app.command("query")
def memory_query(
    query: str = typer.Argument(..., help="Memory query"),
    limit: int = typer.Option(10, "--limit", help="Maximum results"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    _emit(store.query_memory(query, limit=limit))


@memory_app.command("export")
def memory_export(
    out: Path = typer.Option(..., "--out", help="Export JSONL path"),
) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    rows = [
        {
            "memory_id": row["memory_id"],
            "kind": row["kind"],
            "content": row["content"],
            "source": row["source"],
            "confidence": row["confidence"],
            "metadata": json.loads(row["metadata_json"]),
        }
        for row in store.conn.execute("SELECT * FROM memory ORDER BY created_at DESC").fetchall()
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    _emit({"output_path": str(out), "rows": len(rows)})


@memory_app.command("import")
def memory_import(path: Path = typer.Argument(..., help="Import JSONL path")) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        store.add_memory(
            kind=row.get("kind", "note"),
            content=row["content"],
            source=row.get("source"),
            confidence=float(row.get("confidence", 0.5)),
            metadata=row.get("metadata", {}),
        )
        count += 1
    _emit({"imported": count})


@memory_app.command("prune")
def memory_prune(limit: int = typer.Option(1000, "--limit", help="Keep at most N memory rows")) -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    rows = store.conn.execute("SELECT memory_id FROM memory ORDER BY created_at DESC").fetchall()
    if len(rows) <= limit:
        _emit({"deleted": 0, "remaining": len(rows)})
        return
    delete_ids = [row["memory_id"] for row in rows[limit:]]
    store.conn.executemany("DELETE FROM memory WHERE memory_id = ?", [(item,) for item in delete_ids])
    store.conn.commit()
    _emit({"deleted": len(delete_ids), "remaining": limit})


@config_app.command("show")
def config_show() -> None:
    config, store, runner, indexer, datasets, jobs, worker, training = _runtime()
    _emit(config.model_dump(mode="json"))


@config_app.command("init")
def config_init(path: Optional[Path] = typer.Option(None, "--path", help="Target config path")) -> None:
    path = path or (Path("~/.animica/ena").expanduser() / "config.toml")
    _emit({"config_path": str(save_default_config(path))})


@artifacts_app.command("list")
def artifacts_list(limit: int = typer.Option(50, "--limit", help="Maximum artifacts")) -> None:
    config, store, operator = _operator_runtime()
    _emit(operator.list_artifacts(limit=limit))


@artifacts_app.command("show")
def artifacts_show(artifact_id: str = typer.Argument(..., help="Artifact id")) -> None:
    config, store, operator = _operator_runtime()
    _emit(operator.show_artifact(artifact_id))


@artifacts_app.command("verify")
def artifacts_verify(artifact_id: str = typer.Argument(..., help="Artifact id")) -> None:
    config, store, operator = _operator_runtime()
    result = operator.verify_artifact(artifact_id)
    _emit(result)
    if not result.get("ok", False):
        raise typer.Exit(code=1)


@runs_app.command("list")
def runs_list(limit: int = typer.Option(50, "--limit", help="Maximum runs")) -> None:
    config, store, operator = _operator_runtime()
    _emit(operator.list_runs(limit=limit))


@runs_app.command("show")
def runs_show(session_id: str = typer.Argument(..., help="Agent run/session id")) -> None:
    config, store, operator = _operator_runtime()
    _emit(operator.show_run(session_id))


@credits_app.command("show")
def credits_show(
    miner_address: Optional[str] = typer.Option(None, "--miner-address", help="Miner or worker identity"),
    limit: int = typer.Option(20, "--limit", help="Maximum ledger entries"),
) -> None:
    config, store, operator = _operator_runtime()
    _emit(operator.credits_show(miner_address=miner_address, limit=limit))


@mining_app.command("status")
def mining_status(
    miner_address: Optional[str] = typer.Option(None, "--miner-address", help="Miner or worker identity"),
) -> None:
    config, store, operator = _operator_runtime()
    _emit(operator.mining_status(miner_address=miner_address))


@app.command("doctor")
def doctor(
    check_model: bool = typer.Option(False, "--check-model", help="Call the configured model provider smoke tests"),
    check_embeddings: bool = typer.Option(False, "--check-embeddings", help="Call the configured embedding provider smoke tests"),
) -> None:
    config, store, operator = _operator_runtime()
    result = operator.doctor(check_model=check_model, check_embeddings=check_embeddings)
    _emit(result)
    if not result.get("ok", False):
        raise typer.Exit(code=1)


@app.command("verify")
def verify(run_demo: bool = typer.Option(False, "--demo", help="Also run the local ENA demo workflow")) -> None:
    config, store, operator = _operator_runtime()
    result = operator.verify(run_demo=run_demo)
    _emit(result)
    if not result.get("ok", False):
        raise typer.Exit(code=1)


@app.command("demo")
def demo(work_dir: Optional[Path] = typer.Option(None, "--work-dir", help="Optional demo workspace")) -> None:
    config, store, operator = _operator_runtime()
    _emit(operator.demo(work_dir=work_dir))


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8787, "--port", help="Bind port"),
) -> None:
    uvicorn.run(create_app(str(_state.config_path) if _state.config_path else None), host=host, port=port, log_level="info")


@prove_app.command("job")
def prove_job(job_id: str = typer.Argument(..., help="Job ID")) -> None:
    jobs_verify(job_id)


@prove_app.command("dataset")
def prove_dataset(path: Path = typer.Argument(..., help="Dataset path")) -> None:
    result = _runtime()[4].validate(path)
    if not result["ok"]:
        _emit(result)
        raise typer.Exit(code=1)
    _emit(result)
