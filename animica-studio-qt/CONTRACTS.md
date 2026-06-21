# Animica Studio Qt — CONTRACTS (single source of truth)

This document specifies the **public API** that every module is built against.
The foundation modules (`config`, `models`, `db`, `app`) are implemented and
verified headless. Service / agent / UI agents MUST match the signatures below
exactly so the codebase integrates.

Hard rules (apply everywhere):
- **PySide6** only (never PyQt6).
- **Never `import git`** — `GitService` uses `subprocess` only (GitPython is not installed).
- Every file must import headless: `QT_QPA_PLATFORM=offscreen`.
- Type hints, error handling, no placeholder/TODO stubs, no hardcoded secrets.
- Redact secrets in logs via `config.redact(...)`.
- Path-traversal protection + project sandboxing in any file op.
- Ignore globs: `.git`, `node_modules`, `.venv`, `dist`, `build`, `target`, `__pycache__`.
- Lazy-import UI inside functions where a module must import before the UI exists.

Verify command:
```
cd /root/animica/animica-studio-qt && \
QT_QPA_PLATFORM=offscreen /root/animica/.venv/bin/python -c \
"import animica_studio.app, animica_studio.db, animica_studio.models, animica_studio.config"
```

---

## 1. `animica_studio.config`

### Constants
- `APP_NAME = "Animica Studio Qt"`, `ORG_NAME = "Animica"`.
- `DEFAULT_SYSTEM_PROMPT: str` — the built-in agent persona (begins
  *"You are Animica Studio Agent, an expert autonomous software engineer running inside Animica Studio Qt…"*). Use this; do not redefine.
- `DEFAULT_MODELS: tuple[str, ...]` = `("anm-fast-8b", "anm-pro-70b")`.
- `ENDPOINT_PRESETS: tuple[tuple[str, ProviderKind], ...]` — preset endpoints
  (`https://pool.animica.org/v1`, `http://localhost:8000/v1`,
  `https://api.animica.org/v1`, `http://localhost:11434/v1`).
- `ECOSYSTEM_LINKS: tuple[tuple[str, str], ...]` — `(label, url)` pairs:
  Animica, Pool, Wallet, Chat, Train. (No trade/buy/solana.)

### `ProviderKind(str, Enum)`
Values: `ANIMICA="animica"`, `OPENAI="openai"`, `OLLAMA="ollama"`.
Classmethod `from_value(value) -> ProviderKind` (defaults to `ANIMICA`).

### `redact(text: str, *secrets: str) -> str`
Masks the supplied concrete secrets **and** secret-looking `key=value` pairs
(api_key/authorization/bearer/secret/token/password/private_key). Use in ALL logs.

### `@dataclass Paths`
- `app_data_dir: Path`
- classmethod `resolve() -> Paths` — via `QStandardPaths.AppDataLocation` with
  pure-Python fallback; always ends in `…/Animica/Animica Studio Qt`.
- `ensure() -> Paths` — mkdir the app data dir, returns self.
- properties: `config_path -> Path` (`settings.json`), `db_path -> Path`
  (`studio.db`), `snapshots_dir -> Path`, `logs_dir -> Path`.

### `@dataclass Settings`
Fields (with defaults):
- `endpoint: str = "https://pool.animica.org/v1"`
- `api_key: str = ""`
- `model: str = "anm-fast-8b"`
- `provider_kind: ProviderKind = ProviderKind.ANIMICA`
- `streaming: bool = True`
- `temperature: float = 0.3`
- `max_tokens: int = 4096`
- `request_timeout: float = 120.0`
- `system_prompt_override: Optional[str] = None`
- `autonomy_level: AutonomyLevel = AutonomyLevel.SUGGEST`
- `theme: str = "dark"`
- `extra: dict[str, Any] = {}`

Properties / methods:
- `system_prompt -> str` — override if non-empty else `DEFAULT_SYSTEM_PROMPT`.
- `chat_completions_url -> str` — `endpoint.rstrip('/') + "/chat/completions"`.
- `models_url -> str` — `…/models`.
- `to_dict() -> dict`, `redacted_dict() -> dict` (api_key masked),
  classmethod `from_dict(data) -> Settings`.
- classmethod `load(paths=None) -> Settings`, `save(paths=None) -> None`
  (atomic JSON write to `Paths.config_path`).

---

## 2. `animica_studio.models`

Helpers: `new_id() -> str` (uuid4 hex), `now_ts() -> float` (epoch seconds).

### Enums
- `AutonomyLevel(str, Enum)`: `CHAT_ONLY="chat_only"`, `SUGGEST="suggest"`,
  `APPLY_WITH_APPROVAL="apply_with_approval"`, `FULL_AGENT="full_agent"`,
  `AUTONOMOUS_LOOP="autonomous_loop"`.
  - `from_value(v) -> AutonomyLevel` (defaults `SUGGEST`).
  - props: `label: str`, `rank: int`, `allows_tools: bool` (False only for
    CHAT_ONLY), `auto_apply: bool` (True for FULL_AGENT/AUTONOMOUS_LOOP).
- `MessageRole(str, Enum)`: `SYSTEM`, `USER`, `ASSISTANT`, `TOOL`;
  `from_value(v)`.
- `ToolStatus(str, Enum)`: `PENDING`, `RUNNING`, `OK`, `ERROR`, `DENIED`,
  `AWAITING_APPROVAL`.
- `ApprovalDecision(str, Enum)`: `APPROVE_ONCE="approve_once"`, `DENY="deny"`,
  `ALWAYS_ALLOW="always_allow"`.

### Dataclasses
- `Session(id, title="New Chat", project_id=None, model=None,
  autonomy_level=SUGGEST, created_at, updated_at, metadata={})`;
  `to_row() -> dict`.
- `Message(id, session_id="", role=USER, content="", name=None,
  tool_call_id=None, created_at, token_count=None, metadata={})`;
  `to_openai() -> dict` (OpenAI chat message; adds tool_call_id/name for TOOL).
- `Project(id, name="", root_path="", project_type="unknown", description="",
  last_opened_at, created_at, metadata={})`.
- `ToolCall(id, session_id="", name="", arguments={}, status=PENDING,
  output="", error=None, risky=False, created_at, finished_at=None)`;
  `to_dict()`.
- `CommandApproval(id, project_id=None, command="", category="general",
  decision=APPROVE_ONCE, always=False, created_at)`; `to_dict()`.
- `FileIndexEntry(id, project_id="", path="", language="", size=0, mtime=0.0,
  summary="", symbols=[], content_excerpt="", indexed_at)`.
  (`path` is **relative to project root**.)
- `ToolResult(ok=True, output="", error=None, data={})` — **uniform tool return**.
  - classmethods `success(output="", **data)`, `failure(error, output="", **data)`.
  - `to_dict()`.
- `PlanStep(index=0, title="", detail="", done=False)`; `to_dict()`.
- `FileDiff(path="", old_text="", new_text="", is_new=False, is_delete=False,
  is_rename=False, new_path=None)`; `to_dict()`. (Used by the diff viewer.)
- `CodeBlock(language, code, start, end)`.

### Markdown helpers
- `extract_code_blocks(text) -> list[CodeBlock]`.
- `strip_code_fences(text) -> str`.

---

## 3. `animica_studio.db.Database`

Construct: `Database(path: str | Path)` — opens sqlite with
`check_same_thread=False`, WAL, `foreign_keys=ON`, guarded by an `RLock`.
`SCHEMA_VERSION = 1`. Tables: `sessions, messages, projects, settings,
tool_calls, command_approvals, files_index`. Schema is created idempotently.

`close()`, context-manager (`__enter__/__exit__`).

### Settings KV
- `get_setting(key, default=None) -> Optional[str]`
- `set_setting(key, value) -> None`
- `get_setting_json(key, default=None) -> Any`
- `set_setting_json(key, value) -> None`

### Projects
- `upsert_project(project: Project) -> Project` (unique on `root_path`)
- `list_projects(limit=100) -> list[Project]` (by `last_opened_at` desc)
- `get_project(project_id) -> Optional[Project]`
- `get_project_by_path(root_path) -> Optional[Project]`
- `delete_project(project_id) -> None`

### Sessions
- `create_session(session: Optional[Session]=None, **kwargs) -> Session`
- `list_sessions(project_id=None, limit=200) -> list[Session]`
- `get_session(session_id) -> Optional[Session]`
- `update_session(session_id, **fields) -> None`
  (allowed: title, project_id, model, autonomy_level, updated_at, metadata)
- `touch_session(session_id) -> None`
- `delete_session(session_id) -> None`

### Messages
- `add_message(message: Optional[Message]=None, **kwargs) -> Message`
  (also touches the session)
- `list_messages(session_id, limit=1000) -> list[Message]` (created_at asc)
- `delete_messages(session_id) -> None`

### Tool calls
- `log_tool_call(tool_call: ToolCall) -> ToolCall` (upsert by id; updates
  status/output/error/finished_at on conflict)
- `list_tool_calls(session_id, limit=500) -> list[ToolCall]`

### Command approvals
- `add_command_approval(approval: CommandApproval) -> CommandApproval`
- `list_command_approvals(project_id=None, only_always=False) -> list[CommandApproval]`
- `find_always_allow(category, project_id=None) -> bool`

### File index
- `upsert_file_index(entry: FileIndexEntry) -> FileIndexEntry` (unique
  `(project_id, path)`)
- `search_file_index(project_id, query, limit=50) -> list[FileIndexEntry]`
  (LIKE over path/summary/symbols/content_excerpt)
- `list_file_index(project_id, limit=5000) -> list[FileIndexEntry]`
- `clear_file_index(project_id) -> None`

---

## 4. `animica_studio.app`

- `load_stylesheet() -> str` — returns `assets/styles.qss` text.
- `class AnimicaStudioApp`: `__init__(argv=None)` sets up QApplication, app
  metadata, window icon, applies QSS, loads `Settings`. `run() -> int` lazily
  imports `animica_studio.ui.main_window.MainWindow`, builds the agent engine
  (best-effort), shows the window, runs the event loop.
- `main(argv=None) -> int` — entrypoint (used by `main.py` and gui-script).

**UI agents:** `MainWindow` MUST live at
`animica_studio/ui/main_window.py` with class name `MainWindow` and constructor
`MainWindow(db, settings, agent_engine)` (keyword-compatible).

**Agent agents:** `AgentEngine` MUST live at `animica_studio/agent/engine.py`
with constructor accepting keyword args `settings=` and `db=` (see §5).

---

## 5. Services layer — `animica_studio.services.*`

All file paths returned/accepted by services are **relative to the project
root** unless noted. Every service guards against path traversal / sandbox
escapes. All these are to be **built by other agents** to these signatures.

### 5.1 `services/animica_client.py` → `AnimicaClient`
```python
class AnimicaClient:
    def __init__(self, settings: Settings) -> None: ...

    # Sync, blocking. Returns the full assistant text. If on_chunk is given and
    # stream=True, on_chunk(delta:str) is called for each token delta.
    def chat(
        self,
        messages: list[dict] | list[Message],
        *,
        stream: bool = True,
        on_chunk: Optional[Callable[[str], None]] = None,
        tools: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model: Optional[str] = None,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> str: ...

    # Async variant with the same contract.
    async def achat(self, messages, *, stream=True, on_chunk=None, tools=None,
                    temperature=None, max_tokens=None, model=None,
                    cancel=None) -> str: ...

    def list_models(self) -> list[str]: ...      # GET {endpoint}/models
    def test_connection(self) -> tuple[bool, str]: ...  # (ok, message)
```
- POSTs OpenAI-compatible `{endpoint}/chat/completions`
  (`settings.chat_completions_url`). Auth: `Authorization: Bearer {api_key}` when
  set. Honors `provider_kind` (animica/openai identical; ollama tolerant of
  `/v1` base). Uses `httpx`. Accepts both raw OpenAI dicts and `Message`
  objects (call `.to_openai()`). Must redact secrets in any logging.
- Streaming parses SSE `data:` lines, `[DONE]` terminator, accumulates
  `choices[0].delta.content`.

### 5.2 `services/project_service.py` → `ProjectService`
```python
class ProjectService(QObject):           # QObject so it can emit signals
    fileTreeChanged = Signal()
    projectOpened = Signal(str)          # root_path

    def __init__(self, db: Optional[Database] = None) -> None: ...
    def open_project(self, path: str) -> Project: ...   # sets sandbox root
    @property
    def root(self) -> Optional[Path]: ...
    def is_open(self) -> bool: ...
    def resolve(self, rel_path: str) -> Path: ...    # raises on traversal escape
    def file_tree(self, rel_dir: str = "") -> list[dict]: ...
        # each node: {"name","path","is_dir","size","children"?}; respects ignore globs
    def read_file(self, rel_path: str, max_bytes: int = 1_000_000) -> str: ...
    def write_file(self, rel_path: str, content: str) -> None: ...
    def create_file(self, rel_path: str, content: str = "") -> None: ...
    def delete_file(self, rel_path: str) -> None: ...
    def rename_file(self, src_rel: str, dst_rel: str) -> None: ...
    def detect_project_type(self) -> str: ...    # python|node|rust|go|... |unknown
    def summarize_project(self) -> str: ...      # human-readable overview
    def snapshot(self, rel_paths: list[str]) -> str: ...   # returns snapshot id (rollback)
    def restore_snapshot(self, snapshot_id: str) -> None: ...
```
- Sandbox: every op runs through `resolve()`, which rejects absolute paths,
  `..` escapes, and symlinks leaving the root. Ignore globs enforced in tree/scan.

### 5.3 `services/git_service.py` → `GitService`  (subprocess only)
```python
class GitService:
    def __init__(self, root: str | Path) -> None: ...
    def is_repo(self) -> bool: ...
    def status(self) -> str: ...                 # porcelain summary
    def status_entries(self) -> list[dict]: ...  # [{"path","index","worktree"}]
    def diff(self, rel_path: Optional[str] = None, staged: bool = False) -> str: ...
    def commit(self, message: str, add_all: bool = True) -> tuple[bool, str]: ...
    def current_branch(self) -> Optional[str]: ...
    def init(self) -> tuple[bool, str]: ...
```
- Implemented with `subprocess.run(["git", ...], cwd=root, capture_output=True)`.
  Never `import git`. Returns `(ok, output_or_error)` for mutating ops.

### 5.4 `services/command_runner.py` → `CommandRunner`
```python
RISKY_CATEGORIES = (
    "destructive",   # rm -rf, del, format, mkfs, dd, truncate, > file
    "install",       # pip/npm/yarn/pnpm/cargo/apt/brew/go install
    "docker",        # docker/docker-compose/podman/kubectl
    "deploy",        # deploy/publish/push to prod, terraform apply, ssh
    "wallet",        # wallet/keygen/private key/seed/sign/transfer
    "network",       # curl|bash, wget|sh piping to a shell
    "privilege",     # sudo, su, chmod 777
)

class CommandRunner(QObject):
    outputReceived = Signal(str)          # streamed stdout/stderr chunk
    started = Signal(str)                  # command string
    finished = Signal(int)                 # exit code
    errorOccurred = Signal(str)

    def __init__(self, cwd: Optional[str] = None) -> None: ...
    def set_cwd(self, cwd: str) -> None: ...
    @staticmethod
    def is_risky(cmd: str) -> bool: ...
    @staticmethod
    def classify(cmd: str) -> Optional[str]: ...   # returns category or None
    def run(self, cmd: str, *, cwd: Optional[str] = None,
            timeout: Optional[float] = None) -> None: ...   # async via QProcess; streams
    def run_blocking(self, cmd: str, *, cwd=None,
                     timeout: Optional[float] = None) -> tuple[int, str]: ...
    def stop(self) -> None: ...
```
- `run()` uses `QProcess`, merges channels, emits `outputReceived` per chunk and
  `finished(code)`. Approval is the caller's responsibility (agent/UI) — the
  runner only *classifies* risk via `is_risky`/`classify`.

### 5.5 `services/indexer.py` → `Indexer`
```python
class Indexer:
    IGNORE_GLOBS = (".git", "node_modules", ".venv", "dist", "build",
                    "target", "__pycache__", ".mypy_cache", ".pytest_cache")
    def __init__(self, project_service: ProjectService,
                 db: Optional[Database] = None) -> None: ...
    def scan(self, project_id: str,
             progress: Optional[Callable[[int, int], None]] = None) -> int: ...
        # walks root, builds FileIndexEntry rows (relative paths), returns count
    def index_file(self, project_id: str, rel_path: str) -> Optional[FileIndexEntry]: ...
    def search(self, project_id: str, query: str, limit: int = 50) -> list[FileIndexEntry]: ...
    def summarize(self, rel_path: str) -> str: ...   # short per-file summary
    @staticmethod
    def detect_language(rel_path: str) -> str: ...
```
- Respects `IGNORE_GLOBS`; skips binary/oversized files; persists via
  `Database.upsert_file_index`.

---

## 6. Agent layer — `animica_studio.agent.*`

### 6.1 `agent/tools.py` → `ToolManager` + registry
Every tool returns a `ToolResult` (`models.ToolResult`).
```python
class ToolContext:
    project_service: ProjectService
    git_service: GitService
    command_runner: CommandRunner
    indexer: Indexer
    db: Optional[Database]
    session_id: Optional[str]
    approve: Callable[[str, str], bool]   # (command_or_action, category) -> allowed

class ToolManager:
    def __init__(self, ctx: ToolContext) -> None: ...
    def list_tools(self) -> list[dict]: ...           # OpenAI tool schemas
    def is_risky(self, name: str, arguments: dict) -> bool: ...
    def call(self, name: str, arguments: dict) -> ToolResult: ...
```

**TOOL registry (names are stable strings):**
| name | risky | purpose |
|------|-------|---------|
| `read_file` | no | read a file (rel path) |
| `write_file` | no* | overwrite/create a file with content |
| `patch_file` | no* | apply a unified-diff / search-replace patch |
| `list_files` | no | list dir / tree |
| `search_project` | no | keyword search via Indexer |
| `create_file` | no* | create a new file |
| `delete_file` | **yes** | delete a file (destructive) |
| `rename_file` | no* | rename/move a file |
| `run_command` | **yes** | run a shell command (risk also re-checked by `CommandRunner.is_risky`) |
| `git_status` | no | git status |
| `git_diff` | no | git diff |
| `git_commit` | **yes** | create a commit |
| `install_dependency` | **yes** | install a package (install category) |
| `detect_project_type` | no | project type string |
| `summarize_project` | no | project overview |

`*` Write/patch/create/rename are **gated by autonomy level**: under
`SUGGEST`/`APPLY_WITH_APPROVAL` they produce a `FileDiff` for the diff viewer and
require accept; under `FULL_AGENT`/`AUTONOMOUS_LOOP` non-risky writes auto-apply.
`delete_file`, `run_command`, `git_commit`, `install_dependency` ALWAYS require
approval regardless of autonomy (unless an `always_allow` approval is recorded
for the project/category via `Database.find_always_allow`).

### 6.2 `agent/engine.py` → `AgentEngine(QObject)`
```python
class AgentEngine(QObject):
    # SIGNALS (exact names + args — UI binds to these)
    assistantChunk = Signal(str)             # streaming token delta
    assistantMessage = Signal(str)           # final assistant message text
    toolCallStarted = Signal(dict)           # ToolCall.to_dict()
    toolCallFinished = Signal(dict)          # ToolCall.to_dict() (with result)
    approvalRequested = Signal(str, object)  # (command_or_action, callback: Callable[[bool], None])
    planReady = Signal(list)                 # list[PlanStep-as-dict]
    statusChanged = Signal(str)              # human status text
    errorOccurred = Signal(str)
    diffProposed = Signal(list)              # list[FileDiff-as-dict] for diff viewer
    busyChanged = Signal(bool)

    def __init__(self, settings: Settings, db: Optional[Database] = None,
                 *, project_service: Optional[ProjectService] = None,
                 tool_manager: Optional[ToolManager] = None,
                 client: Optional[AnimicaClient] = None) -> None: ...

    # SLOTS / methods (UI calls these)
    def send_user_message(self, text: str) -> None: ...   # runs on a worker thread
    def set_project(self, path: str) -> None: ...
    def set_session(self, session_id: str) -> None: ...
    def set_autonomy(self, level: AutonomyLevel) -> None: ...
    def set_settings(self, settings: Settings) -> None: ...
    def stop(self) -> None: ...               # cancel current run
    @property
    def is_busy(self) -> bool: ...
```
- **Threading:** `send_user_message` must not block the GUI thread — run the
  client + tool loop on a `QThread`/worker and emit signals back. `stop()` sets a
  cancel flag honored by `AnimicaClient(..., cancel=...)` and the tool loop.
- **Approval flow:** when a risky tool/command is reached the engine emits
  `approvalRequested(action, callback)`; the UI shows the Approve once / Deny /
  Always allow dialog and invokes `callback(allowed: bool)`. "Always allow"
  persists a `CommandApproval(always=True, category=…)` via the DB.
- **Planner/executor:** for large tasks the engine produces a `list[PlanStep]`
  (emit `planReady`), then executes steps, emitting tool call + diff signals.
  In `CHAT_ONLY` no tools run (`AutonomyLevel.allows_tools is False`).

---

## 7. UI layer — `animica_studio.ui.*`

`MainWindow` lives at `animica_studio/ui/main_window.py`.
```python
class MainWindow(QMainWindow):
    def __init__(self, db: Database, settings: Settings,
                 agent_engine: Optional[AgentEngine] = None) -> None: ...
```
Layout (use `QSplitter`s):
- **Top toolbar** (objectName `TopToolBar`): open project, new chat, run, model
  picker, autonomy-level selector, settings.
- **Left sidebar** (objectName `Sidebar`): projects list, file tree, recent
  chats, ecosystem links (`config.ECOSYSTEM_LINKS`).
- **Center**: editor tabs + chat toggle + diff viewer.
- **Right panel** (objectName `RightPanel`): agent plan, tool calls, context,
  settings shortcut, build status.
- **Bottom panel** (objectName `BottomPanel`): terminal, problems, logs.
- **Status bar**: status text (objectNames `StatusOk`/`StatusBusy`/`StatusError`).

### Panel classes (other UI agents build these to these signatures)
- `ChatPanel(agent_engine: AgentEngine, db: Database)` — binds to engine signals
  (`assistantChunk`, `assistantMessage`, `toolCallStarted/Finished`,
  `approvalRequested`, `statusChanged`, `errorOccurred`); calls
  `agent_engine.send_user_message(text)`. Renders markdown (use
  `models.extract_code_blocks`). Bubble objectNames:
  `ChatBubbleUser/Assistant/Tool`, badges `RoleBadgeUser/Assistant/Tool`.
- `EditorPanel(project_service: ProjectService)` — tabbed `QPlainTextEdit`
  (objectName `Editor`, class `code`); `open_file(rel_path)`, signal
  `dirtyChanged(str path, bool dirty)`, `saveRequested(str path)`.
- `FileTreePanel(project_service: ProjectService)` — signal
  `fileActivated(str path)` (rel path); `refresh()`.
- `TerminalPanel(command_runner: CommandRunner)` — objectName `Terminal`;
  binds `outputReceived/started/finished`; `run(cmd)`.
- `SettingsDialog(settings: Settings, db: Database)` — edits all `Settings`
  fields incl. endpoint presets (`ENDPOINT_PRESETS`), provider kind, model,
  autonomy, system prompt override; `accepted` → returns updated `Settings`
  (method `result_settings() -> Settings`) and saves.
- `DiffViewer(diffs: list[FileDiff] | None = None)` — shows per-file
  old/new (objectNames `DiffOld`/`DiffNew`); signals
  `accepted(list)`, `rejected(list)` (accepted/rejected `FileDiff` paths);
  `set_diffs(list[FileDiff])`, per-file accept/reject + rollback snapshot.
- `ProjectWizard(templates_service)` — new-project templates; signal
  `projectCreated(str root_path)`. (`templates_service` provided by the
  project/templates agent; minimal interface: `list_templates() -> list[dict]`,
  `create(template_id, dest_dir, name) -> str root_path`.)

### QSS object names / classes available (from `assets/styles.qss`)
- Containers: `Sidebar`, `RightPanel`, `BottomPanel`, `TopToolBar`, `Panel`,
  dynamic property `panel="true"`.
- Buttons: classes `primary`, `ghost`, `danger`, `accent` (also as dynamic
  property `variant=`).
- Labels: `BrandTitle`, `SectionHeader`, `StatusOk`, `StatusBusy`,
  `StatusError`, `PlanStepDone`, `PlanStepPending`, role `link`.
- Code areas: `Editor`, `CodeArea`, class `code`, `Terminal`.
- Chat: `ChatBubbleUser/Assistant/Tool`, `RoleBadgeUser/Assistant/Tool`.
- Diff: `DiffOld`, `DiffNew`.

Set these via `widget.setObjectName(...)` or
`widget.setProperty("class"/"variant", ...)` then re-polish.

---

## 8. Module map (where each agent writes)
```
animica_studio/
  __init__.py            (foundation)  version
  config.py              (foundation)  Settings, Paths, ProviderKind, redact
  models.py              (foundation)  dataclasses + enums + helpers
  db.py                  (foundation)  Database
  app.py                 (foundation)  AnimicaStudioApp, main
  assets/styles.qss      (foundation)  dark theme
  services/
    animica_client.py    AnimicaClient
    project_service.py   ProjectService
    git_service.py       GitService          (subprocess only)
    command_runner.py    CommandRunner
    indexer.py           Indexer
    templates_service.py TemplatesService    (project wizard)
  agent/
    tools.py             ToolManager, ToolContext, TOOL registry
    engine.py            AgentEngine
  ui/
    main_window.py       MainWindow
    chat_panel.py        ChatPanel
    editor_panel.py      EditorPanel
    file_tree_panel.py   FileTreePanel
    terminal_panel.py    TerminalPanel
    settings_dialog.py   SettingsDialog
    diff_viewer.py       DiffViewer
    project_wizard.py    ProjectWizard
main.py                  entrypoint → animica_studio.app:main
```
