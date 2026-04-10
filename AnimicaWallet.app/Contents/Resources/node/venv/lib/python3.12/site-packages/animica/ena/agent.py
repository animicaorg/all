from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .config import load_ena_config
from .datasets import DatasetManager
from .ingest import Crawler, Fetcher, export_jsonl
from .jobs import JobManager, WorkerEngine
from .models import AgentTrace, AutonomyLevel, Citation, EnaConfigModel, SearchHit, SessionRecord, TaskSpec
from .providers import ProviderError, ToolDefinition, create_model_provider
from .retrieval import IndexManager
from .store import EnaStore
from .text import keyword_terms, normalize_text, stable_id, summarize_passages, utc_now_iso


class ToolBox:
    def __init__(self, store: EnaStore, config: EnaConfigModel):
        self.store = store
        self.config = config
        self.index = IndexManager(store, config)
        self.datasets = DatasetManager(store, config)
        self.jobs = JobManager(store, config)
        self.worker = WorkerEngine(store, config)

    def index_context(self, path: Path, *, embedding_provider: Optional[str] = None) -> Dict[str, Any]:
        result = self.index.index_path(path, reset=False, embedding_provider_name=embedding_provider)
        self.store.audit("tool.index_context", {"path": str(path), "result": result})
        return result

    def search_context(
        self,
        query: str,
        *,
        index_name: Optional[str] = None,
        limit: int = 8,
        mode: str = "hybrid",
        embedding_provider: Optional[str] = None,
    ) -> List[SearchHit]:
        hits = self.index.search(
            query,
            index_name=index_name,
            limit=limit,
            strategy=mode,
            embedding_provider_name=embedding_provider,
        )
        self.store.audit(
            "tool.search_context",
            {
                "query": query,
                "index_name": index_name,
                "limit": limit,
                "mode": mode,
                "hits": [hit.model_dump(mode="json") for hit in hits],
            },
        )
        return hits

    def list_indexes(self) -> List[Dict[str, Any]]:
        indexes = [item.model_dump(mode="json") for item in self.index.list_indexes()]
        self.store.audit("tool.list_indexes", {"count": len(indexes)})
        return indexes

    def fetch(self, url: str) -> Dict[str, Any]:
        outcome = Fetcher(self.config.network).fetch(url)
        from .ingest import records_from_fetch

        rows = records_from_fetch(outcome)
        export_path = Path(self.config.storage.datasets_dir) / f"{stable_id('fetch', url)}.jsonl"
        export_jsonl(rows, export_path)
        artifact = self.store.put_artifact(
            "fetch_records",
            export_path.read_text(encoding="utf-8"),
            metadata={"url": url},
            suffix=".jsonl",
        )
        payload = {"url": url, "records": rows, "artifact_id": artifact.artifact_id, "output_path": str(export_path)}
        self.store.audit("tool.fetch", payload)
        return payload

    def crawl(self, urls: List[str], *, depth: Optional[int] = None, max_requests: Optional[int] = None) -> Dict[str, Any]:
        crawler = Crawler(Fetcher(self.config.network))
        rows = crawler.crawl(urls, max_depth=depth, max_requests=max_requests)
        export_path = Path(self.config.storage.datasets_dir) / f"{stable_id('crawl', *urls)}.jsonl"
        export_jsonl(rows, export_path)
        artifact = self.store.put_artifact(
            "crawl_records",
            export_path.read_text(encoding="utf-8"),
            metadata={"urls": urls},
            suffix=".jsonl",
        )
        payload = {"records": rows, "artifact_id": artifact.artifact_id, "output_path": str(export_path)}
        self.store.audit("tool.crawl", {"urls": urls, "depth": depth, "max_requests": max_requests, "result": payload})
        return payload

    def read_file(self, path: str, *, max_chars: int = 4000) -> Dict[str, Any]:
        target = Path(path).expanduser().resolve()
        if not target.exists():
            raise FileNotFoundError(path)
        content = target.read_text(encoding="utf-8", errors="ignore")[:max_chars]
        payload = {"path": str(target), "content": content, "truncated": len(content) >= max_chars}
        self.store.audit("tool.read_file", {"path": str(target), "max_chars": max_chars})
        return payload

    def query_memory(self, query: str, *, limit: int = 5) -> List[Dict[str, Any]]:
        results = self.store.query_memory(query, limit=limit)
        self.store.audit("tool.query_memory", {"query": query, "limit": limit, "hits": len(results)})
        return results

    def shell(self, command: str | List[str], *, approved: bool = False, cwd: Optional[Path] = None) -> Dict[str, Any]:
        if not self.config.shell.allow_shell:
            raise PermissionError("shell execution disabled by policy")
        tokens = shlex.split(command) if isinstance(command, str) else command
        normalized = " ".join(tokens)
        if self.config.shell.approval_required and not approved:
            raise PermissionError("shell command requires explicit approval")
        if not self.config.shell.allow_destructive:
            for blocked in self.config.shell.blocked_tokens:
                if blocked.strip() and blocked in normalized:
                    raise PermissionError(f"blocked shell token detected: {blocked.strip()}")
        if self.config.shell.approved_prefixes:
            allowed = any(tokens[: len(prefix)] == prefix for prefix in self.config.shell.approved_prefixes)
            if not allowed:
                raise PermissionError("shell command does not match approved prefixes")
        result = subprocess.run(tokens, cwd=str(cwd or self.config.workspace), capture_output=True, text=True, timeout=120)
        payload = {
            "command": tokens,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        self.store.audit("shell.exec", payload)
        return payload

    def tool_definitions(self) -> List[ToolDefinition]:
        return [
            ToolDefinition(
                name="search_context",
                description="Search indexed repo/docs/dataset context and return cited hits.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "index_name": {"type": "string"},
                        "limit": {"type": "integer"},
                        "mode": {"type": "string", "enum": ["keyword", "semantic", "hybrid"]},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="fetch_url",
                description="Fetch one URL and store normalized records with provenance.",
                parameters={
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="crawl_urls",
                description="Crawl one or more URLs under ENA network policy controls.",
                parameters={
                    "type": "object",
                    "properties": {
                        "urls": {"type": "array", "items": {"type": "string"}},
                        "depth": {"type": "integer"},
                        "max_requests": {"type": "integer"},
                    },
                    "required": ["urls"],
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="read_file",
                description="Read a local file for precise repo or config context.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "max_chars": {"type": "integer"},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="list_indexes",
                description="List available retrieval indexes and their providers.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            ToolDefinition(
                name="query_memory",
                description="Query ENA memory for prior notes or facts.",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
        ]

    def execute_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if name == "search_context":
            hits = self.search_context(
                arguments.get("query", ""),
                index_name=arguments.get("index_name"),
                limit=int(arguments.get("limit", 8)),
                mode=arguments.get("mode", "hybrid"),
            )
            return {"hits": [hit.model_dump(mode="json") for hit in hits]}
        if name == "fetch_url":
            return self.fetch(arguments["url"])
        if name == "crawl_urls":
            return self.crawl(
                list(arguments.get("urls", [])),
                depth=arguments.get("depth"),
                max_requests=arguments.get("max_requests"),
            )
        if name == "read_file":
            return self.read_file(arguments["path"], max_chars=int(arguments.get("max_chars", 4000)))
        if name == "list_indexes":
            return {"indexes": self.list_indexes()}
        if name == "query_memory":
            return {"results": self.query_memory(arguments["query"], limit=int(arguments.get("limit", 5)))}
        raise RuntimeError(f"unknown tool: {name}")


class AgentRunner:
    def __init__(self, config: Optional[EnaConfigModel] = None, store: Optional[EnaStore] = None):
        self.config = config or load_ena_config()
        self.store = store or EnaStore(self.config)
        self.tools = ToolBox(self.store, self.config)

    def plan(self, spec: TaskSpec) -> List[Dict[str, Any]]:
        provider = self._provider(spec)
        schema = {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "goal": {"type": "string"},
                            "tool": {"type": "string"},
                        },
                        "required": ["goal"],
                        "additionalProperties": True,
                    },
                }
            },
            "required": ["steps"],
            "additionalProperties": False,
        }
        try:
            response = provider.structured(
                [
                    {
                        "role": "system",
                        "content": "Plan the task using the available ENA tools when useful. Keep the plan short and actionable.",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": spec.task,
                                "context_paths": spec.context_paths,
                                "urls": spec.urls,
                                "available_tools": [tool.name for tool in self.tools.tool_definitions()],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                schema=schema,
            )
            self._audit_model_interaction(
                "plan",
                provider_name=provider.provider_name,
                model=provider.config.model,
                payload={"task": spec.task, "context_paths": spec.context_paths, "urls": spec.urls},
                result=response.parsed,
            )
            steps = response.parsed["steps"][: spec.max_steps]
            return steps
        except Exception:
            return self._fallback_plan(spec)

    def run(self, spec: TaskSpec) -> Dict[str, Any]:
        session = SessionRecord(
            session_id=stable_id("session", spec.task, utc_now_iso()),
            task=spec.task,
            status="running",
            autonomy=spec.autonomy,
            working_dir=str(self.config.workspace),
            metadata={
                "context_paths": spec.context_paths,
                "urls": spec.urls,
                "model_provider": spec.model_provider or self.config.default_model_provider,
                "model": spec.model,
            },
        )
        self.store.save_session(session)

        provider = self._provider(spec)
        plan = self.plan(spec)
        outputs: Dict[str, Any] = {
            "plan": plan,
            "model_provider": spec.model_provider or self.config.default_model_provider,
            "model": provider.config.model,
        }
        if provider.config.provider == "deterministic":
            return self._run_deterministic(session, spec, outputs)
        gathered_hits: List[SearchHit] = []
        gathered_records: List[Dict[str, Any]] = []
        messages = self._initial_messages(spec, plan)
        tool_definitions = self.tools.tool_definitions()
        final_answer: Optional[str] = None
        final_citations: List[Dict[str, Any]] = []

        try:
            indexes = []
            for path_text in spec.context_paths:
                index_result = self.tools.index_context(Path(path_text))
                indexes.append(index_result)
            if indexes:
                outputs["indexes"] = indexes
                messages.append(
                    {
                        "role": "system",
                        "content": json.dumps({"indexed_context": indexes}, ensure_ascii=False),
                    }
                )

            for step_index in range(1, spec.max_steps + 1):
                trace = AgentTrace(
                    session_id=session.session_id,
                    step_index=step_index,
                    action="agent_loop",
                    status="running",
                    input_payload={"messages": messages[-4:], "plan": plan},
                )
                try:
                    decision = self._decide_next(provider, messages, tool_definitions)
                    if decision["action"] == "tool":
                        tool_name = decision["tool_name"]
                        arguments = decision.get("arguments", {})
                        tool_result = self.tools.execute_tool(tool_name, arguments)
                        trace.tool_name = tool_name
                        trace.output_payload = tool_result
                        trace.status = "completed"
                        self.store.add_trace(trace)
                        messages.append(
                            {
                                "role": "assistant",
                                "content": json.dumps(
                                    {"tool_request": {"name": tool_name, "arguments": arguments}},
                                    ensure_ascii=False,
                                ),
                            }
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "name": tool_name,
                                "content": json.dumps(tool_result, ensure_ascii=False),
                            }
                        )
                        if tool_name == "search_context":
                            hits = [SearchHit.model_validate(item) for item in tool_result.get("hits", [])]
                            gathered_hits.extend(hits)
                        elif tool_name in {"fetch_url", "crawl_urls"}:
                            gathered_records.extend(tool_result.get("records", []))
                        continue

                    if decision["action"] == "final":
                        final_answer = normalize_text(decision.get("answer", ""))
                        break
                except Exception as exc:  # noqa: BLE001
                    trace.status = "failed"
                    trace.output_payload = {"error": str(exc)}
                    self.store.add_trace(trace)
                    messages.append({"role": "system", "content": f"Tool/decision error: {exc}"})
                    if step_index >= self.config.agent_retry_limit + 1:
                        raise
                    continue

            synthesis = self._synthesize(provider, spec.task, gathered_hits, gathered_records)
            if not final_answer:
                final_answer = synthesis["answer"]
            final_citations = synthesis["citations"]
            outputs["answer"] = final_answer
            outputs["citations"] = final_citations
            outputs["evidence"] = {
                "search_hits": [hit.model_dump(mode="json") for hit in gathered_hits[:12]],
                "records": gathered_records[:12],
            }
            if spec.response_schema:
                structured_final = provider.extract(
                    json.dumps(
                        {
                            "task": spec.task,
                            "answer": final_answer,
                            "citations": final_citations,
                            "evidence": outputs["evidence"],
                        },
                        ensure_ascii=False,
                    ),
                    spec.response_schema,
                    instruction="Return the final answer in the requested schema using only the supplied evidence.",
                )
                outputs["structured_response"] = structured_final
                if spec.output_format == "json":
                    outputs["answer"] = structured_final
                else:
                    outputs["answer"] = json.dumps(structured_final, ensure_ascii=False)
        except Exception:
            session.status = "failed"
            session.updated_at = utc_now_iso()
            self.store.save_session(session)
            raise

        session.status = "completed"
        session.updated_at = utc_now_iso()
        session.summary = outputs.get("answer")
        if spec.save_as:
            out_path = Path(spec.save_as)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if spec.output_format == "json":
                out_path.write_text(json.dumps(outputs, indent=2), encoding="utf-8")
            else:
                out_path.write_text(outputs.get("answer", ""), encoding="utf-8")
            outputs["saved_to"] = str(out_path)
        artifact = self.store.put_artifact(
            "agent_run",
            json.dumps(outputs, indent=2),
            metadata={"session_id": session.session_id},
            suffix=".json",
        )
        outputs["artifact_id"] = artifact.artifact_id
        outputs["session_id"] = session.session_id
        self.store.save_session(session)
        return outputs

    def ask(
        self,
        question: str,
        *,
        context_paths: Optional[List[str]] = None,
        urls: Optional[List[str]] = None,
        autonomy: AutonomyLevel = AutonomyLevel.WORKSPACE,
        model_provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.run(
            TaskSpec(
                task=question,
                context_paths=context_paths or [],
                urls=urls or [],
                autonomy=autonomy,
                model_provider=model_provider,
                model=model,
            )
        )

    def _provider(self, spec: TaskSpec):
        provider = create_model_provider(self.config, provider_name=spec.model_provider)
        if spec.model:
            provider.config = provider.config.model_copy(update={"model": spec.model})
        return provider

    def _initial_messages(self, spec: TaskSpec, plan: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "role": "system",
                "content": (
                    "You are ENA, an auditable Animica agent. Use tools when you need evidence. "
                    "Stay grounded in retrieved or fetched context and keep a concise final answer."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": spec.task,
                        "context_paths": spec.context_paths,
                        "urls": spec.urls,
                        "plan": list(plan),
                    },
                    ensure_ascii=False,
                ),
            },
        ]

    def _decide_next(
        self,
        provider,
        messages: Sequence[Dict[str, Any]],
        tool_definitions: Sequence[ToolDefinition],
    ) -> Dict[str, Any]:
        try:
            response = provider.chat(messages, tools=tool_definitions)
            self._audit_model_interaction(
                "decide_chat",
                provider_name=provider.provider_name,
                model=provider.config.model,
                payload={"messages": list(messages), "tools": [tool.name for tool in tool_definitions]},
                result={"tool_calls": [call.name for call in response.tool_calls], "content": response.content},
            )
            if response.tool_calls:
                tool_call = response.tool_calls[0]
                return {"action": "tool", "tool_name": tool_call.name, "arguments": tool_call.arguments}
        except Exception:
            pass

        schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["tool", "final"]},
                "tool_name": {"type": "string"},
                "arguments": {"type": "object"},
                "answer": {"type": "string"},
            },
            "required": ["action"],
            "additionalProperties": True,
        }
        response = provider.structured(
            list(messages)
            + [
                {
                    "role": "system",
                    "content": json.dumps(
                        {
                            "instruction": "Choose the next action. Use action=tool when more evidence is needed. Use action=final when you can answer.",
                            "available_tools": [tool.name for tool in tool_definitions],
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            schema=schema,
        )
        self._audit_model_interaction(
            "decide_structured",
            provider_name=provider.provider_name,
            model=provider.config.model,
            payload={"messages": list(messages), "tools": [tool.name for tool in tool_definitions]},
            result=response.parsed,
        )
        parsed = response.parsed
        return {
            "action": parsed["action"],
            "tool_name": parsed.get("tool_name"),
            "arguments": parsed.get("arguments", {}),
            "answer": parsed.get("answer", ""),
        }

    def _fallback_plan(self, spec: TaskSpec) -> List[Dict[str, Any]]:
        steps: List[Dict[str, Any]] = []
        if spec.urls:
            action = "crawl_urls" if any(word in spec.task.lower() for word in {"crawl", "scrape"}) else "fetch_url"
            steps.append({"goal": "Collect external context", "tool": action})
        if spec.context_paths:
            steps.append({"goal": "Index context paths", "tool": "index_context"})
            steps.append({"goal": "Search indexed context", "tool": "search_context"})
        steps.append({"goal": "Summarize findings", "tool": None})
        return steps[: spec.max_steps]

    def _synthesize(self, provider, query: str, hits: List[SearchHit], records: List[Dict[str, Any]]) -> Dict[str, Any]:
        passages = [hit.excerpt for hit in hits]
        passages.extend(record.get("content_text", "")[:400] for record in records)
        answer = ""
        try:
            if passages:
                answer = provider.summarize(query, passages)
                self._audit_model_interaction(
                    "summarize",
                    provider_name=provider.provider_name,
                    model=provider.config.model,
                    payload={"query": query, "passage_count": len(passages)},
                    result={"answer": answer},
                )
        except ProviderError:
            answer = ""
        if not answer:
            sentences = summarize_passages(query, passages, max_sentences=6)
            if not sentences and hits:
                sentences = [hit.excerpt for hit in hits[:3]]
            answer = " ".join(sentences).strip() or "No relevant evidence found."
        citations: List[Dict[str, Any]] = [
            Citation(source=hit.source, title=hit.title, chunk_id=hit.chunk_id, score=hit.score).model_dump(mode="json")
            for hit in hits[:6]
        ]
        if not citations:
            for record in records[:6]:
                citations.append(
                    Citation(
                        source=record.get("canonical_url") or record.get("url") or "record",
                        title=record.get("title"),
                        score=1.0,
                    ).model_dump(mode="json")
                )
        return {"answer": answer, "citations": citations}

    def _audit_model_interaction(
        self,
        event: str,
        *,
        provider_name: str,
        model: str,
        payload: Dict[str, Any],
        result: Any,
    ) -> None:
        self.store.audit(
            f"model.{event}",
            {
                "provider_name": provider_name,
                "model": model,
                "payload": payload,
                "result": result,
            },
        )

    def _run_deterministic(self, session: SessionRecord, spec: TaskSpec, outputs: Dict[str, Any]) -> Dict[str, Any]:
        gathered_hits: List[SearchHit] = []
        gathered_records: List[Dict[str, Any]] = []
        step_index = 1
        if spec.urls:
            action = "crawl" if any(word in spec.task.lower() for word in {"crawl", "scrape"}) else "fetch"
            trace = AgentTrace(session_id=session.session_id, step_index=step_index, action=action, status="running")
            if action == "crawl":
                result = self.tools.crawl(spec.urls)
                gathered_records.extend(result.get("records", []))
            else:
                result = self.tools.fetch(spec.urls[0])
                gathered_records.extend(result.get("records", []))
            trace.status = "completed"
            trace.output_payload = result
            self.store.add_trace(trace)
            step_index += 1

        indexes = []
        for path_text in spec.context_paths:
            trace = AgentTrace(session_id=session.session_id, step_index=step_index, action="index_context", status="running", input_payload={"path": path_text})
            result = self.tools.index_context(Path(path_text))
            indexes.append(result)
            trace.status = "completed"
            trace.output_payload = result
            self.store.add_trace(trace)
            step_index += 1
        if indexes:
            outputs["indexes"] = indexes

        if spec.context_paths:
            trace = AgentTrace(session_id=session.session_id, step_index=step_index, action="search_context", status="running", input_payload={"query": spec.task})
            hits: List[SearchHit] = []
            for path_text in spec.context_paths:
                index_name = stable_id("index", str(Path(path_text).resolve()))
                hits.extend(self.tools.search_context(spec.task, index_name=index_name, limit=8, mode="keyword"))
            hits.sort(key=lambda item: -item.score)
            gathered_hits = hits[:8]
            trace.status = "completed"
            trace.output_payload = {"hits": [hit.model_dump(mode="json") for hit in gathered_hits]}
            self.store.add_trace(trace)
            step_index += 1

        synthesis = self._synthesize(self._provider(TaskSpec(task=spec.task)), spec.task, gathered_hits, gathered_records)
        outputs["answer"] = synthesis["answer"]
        outputs["citations"] = synthesis["citations"]
        session.status = "completed"
        session.updated_at = utc_now_iso()
        session.summary = outputs.get("answer")
        if spec.save_as:
            out_path = Path(spec.save_as)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if spec.output_format == "json":
                out_path.write_text(json.dumps(outputs, indent=2), encoding="utf-8")
            else:
                out_path.write_text(outputs.get("answer", ""), encoding="utf-8")
            outputs["saved_to"] = str(out_path)
        artifact = self.store.put_artifact(
            "agent_run",
            json.dumps(outputs, indent=2),
            metadata={"session_id": session.session_id},
            suffix=".json",
        )
        outputs["artifact_id"] = artifact.artifact_id
        outputs["session_id"] = session.session_id
        self.store.save_session(session)
        return outputs
