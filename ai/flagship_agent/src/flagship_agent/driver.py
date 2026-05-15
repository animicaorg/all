"""Python pipeline driver for the flagship-agent training stack.

Invoked by ``scripts/train_flagship_agent.sh`` after env validation. Reads
``ai/configs/pipeline.yaml`` and walks each stage, marshalling logging
and resume state.

Each stage is a python script under ``flagship_agent/scripts/<name>.py``
that the driver invokes as a subprocess (so a crashing stage can't
corrupt the driver state). Stage scripts read their inputs from disk and
write outputs deterministically — they do not communicate with the driver
except via the manifest emitted in their pipeline_dir.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from agent_runtime.config import Config, load_run_config
from agent_runtime.logging import RunRecorder, make_run_id

PKG_DIR = Path(__file__).resolve().parent.parent.parent   # ai/flagship_agent


def _resolve_scripts_dir() -> Path:
    """Stage scripts can live in either source-tree or wheel-installed layout.

    Source-tree:   <repo>/ai/flagship_agent/scripts/
    Wheel-install: <sitepackages>/animica/_data/ai/flagship_agent/scripts/
    Operator override: $FLAGSHIP_SCRIPTS_DIR
    """
    override = os.environ.get("FLAGSHIP_SCRIPTS_DIR")
    if override and Path(override).is_dir():
        return Path(override)
    src_tree = PKG_DIR / "scripts"
    if src_tree.is_dir():
        return src_tree
    # Wheel: walk up to find animica/_data/ai/flagship_agent/scripts/.
    try:
        import animica   # type: ignore
        wheel_data = Path(animica.__file__).resolve().parent / "_data" / \
            "ai" / "flagship_agent" / "scripts"
        if wheel_data.is_dir():
            return wheel_data
    except Exception:    # noqa: BLE001
        pass
    return src_tree


SCRIPTS_DIR = _resolve_scripts_dir()


# --------------------------------------------------------------------------- #
# Stage execution                                                             #
# --------------------------------------------------------------------------- #

def _expand_outputs(outputs: list[str], run_id: str) -> list[str]:
    return [o.replace("${run_id}", run_id) for o in outputs]


def _all_outputs_exist(outputs: list[str], pkg_dir: Path) -> bool:
    if not outputs:
        return False
    for o in outputs:
        p = pkg_dir / o
        if o.endswith("/"):
            if not p.is_dir() or not any(p.iterdir()):
                return False
        else:
            if not p.exists():
                return False
    return True


def _stage_script_path(script_name: str) -> Path:
    return SCRIPTS_DIR / script_name


def _run_stage_subprocess(*, stage: dict, run_dir: Path,
                          run_id: str, pkg_dir: Path,
                          env_overrides: Mapping[str, str]
                          ) -> tuple[int, str]:
    """Spawn the stage script as a subprocess; return (exit, captured_log)."""
    script_name = stage["script"]
    script_path = _stage_script_path(script_name)
    if script_name.endswith(".sh"):
        cmd = ["bash", str(script_path)]
    else:
        cmd = [sys.executable, str(script_path)]

    env = dict(os.environ)
    env.update(env_overrides)
    env["FLAGSHIP_RUN_ID"] = run_id
    env["FLAGSHIP_PKG_DIR"] = str(pkg_dir)
    env["FLAGSHIP_REPO_ROOT"] = env.get(
        "ANIMICA_REPO_ROOT", str(pkg_dir.parent.parent))
    env["PYTHONPATH"] = ":".join(filter(None, [
        env.get("PYTHONPATH", ""),
        str(env["FLAGSHIP_REPO_ROOT"] + "/ai/agent_runtime/src"),
        str(env["FLAGSHIP_REPO_ROOT"] + "/ai/flagship_agent/src"),
    ]))

    log_path = run_dir / "_pipeline" / f"{stage['name']}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as logfh:
        proc = subprocess.run(
            cmd, env=env, stdout=logfh, stderr=subprocess.STDOUT,
        )
    captured = log_path.read_text(encoding="utf-8", errors="replace")
    return proc.returncode, captured


# --------------------------------------------------------------------------- #
# Driver                                                                      #
# --------------------------------------------------------------------------- #

def _resolve_run_id(cfg: Config, env: Mapping[str, str]) -> tuple[str, bool]:
    resume_env = cfg.pipeline["mode"]["resume_env_var"]
    resume = env.get(resume_env)
    runs_dir = PKG_DIR / cfg.pipeline["run"]["root"]
    if resume:
        if resume == "latest":
            existing = sorted(runs_dir.glob("flagship-*"))
            if not existing:
                print(f"[driver] no existing runs to resume; minting new id",
                      file=sys.stderr)
            else:
                return existing[-1].name, True
        else:
            p = runs_dir / resume
            if not p.is_dir():
                raise SystemExit(
                    f"resume id {resume!r} not found under {runs_dir}")
            return resume, True
    pattern = cfg.pipeline["run"]["id_pattern"]
    run_id = pattern.replace("${ts}", str(int(time.time()))).replace(
        "${mode}", cfg.mode)
    if "${" in run_id:
        run_id = make_run_id(prefix=run_id.split("-")[0] or "flagship")
    return run_id, False


def _selected_stages(cfg: Config, env: Mapping[str, str]) -> list[dict]:
    all_stages = list(cfg.pipeline["stages"])
    selector_env = cfg.pipeline["stage_selection"]["env_var"]
    raw = env.get(selector_env)
    if not raw:
        return all_stages
    wanted = [s.strip() for s in raw.split(",") if s.strip()]
    selected = [s for s in all_stages if s["name"] in wanted]
    missing = [n for n in wanted if n not in {s["name"] for s in all_stages}]
    if missing:
        print(f"[driver] unknown stages in {selector_env}: {missing}",
              file=sys.stderr)
    return selected


def run_pipeline(cfg: Config, *, run_dir: Path, run_id: str,
                 resumed: bool) -> int:
    stages = _selected_stages(cfg, os.environ)
    if not stages:
        print("[driver] no stages selected; nothing to do", file=sys.stderr)
        return 0
    recorder = RunRecorder(
        run_root=run_dir.parent,
        run_id=run_id,
        mode_requested=os.environ.get("FLAGSHIP_MODE",
                                       cfg.pipeline["mode"]["default"]),
        mode_effective=cfg.mode,
    )
    recorder.start()

    artifacts: list[str] = []
    overall_exit = 0
    for stage in stages:
        name = stage["name"]
        outputs = _expand_outputs(stage.get("outputs", []), run_id)
        # Skippable + resume + all-outputs-exist → skip.
        if resumed and stage.get("skippable", False) and \
                _all_outputs_exist(outputs, PKG_DIR):
            recorder.skip_stage(name, "resume_outputs_present")
            continue
        with recorder.stage(name,
                             inputs=stage.get("inputs", [])) as handle:
            handle.info(f"starting {name}")
            rc, log = _run_stage_subprocess(
                stage=stage, run_dir=run_dir, run_id=run_id,
                pkg_dir=PKG_DIR,
                env_overrides={"FLAGSHIP_STAGE_NAME": name,
                                "FLAGSHIP_STAGE_OUTPUTS":
                                    json.dumps(outputs)},
            )
            for o in outputs:
                handle.add_output(o)
            if rc != 0:
                tail = "\n".join(log.splitlines()[-15:])
                handle.error(f"{name} exited rc={rc}",
                              tail=tail)
                if stage.get("on_fail") == "soft":
                    overall_exit = max(overall_exit, 1)
                    handle.warn("on_fail=soft; continuing")
                    continue
                recorder.finish(exit_code=3,
                                artifacts=artifacts)
                return 3
            artifacts.extend(outputs)
            handle.info(f"{name} ok")
    recorder.finish(exit_code=overall_exit, artifacts=artifacts)
    return overall_exit


# --------------------------------------------------------------------------- #
# Entrypoint                                                                  #
# --------------------------------------------------------------------------- #

def main(argv: Optional[list[str]] = None) -> int:
    cfg = load_run_config_path()
    runs_root = PKG_DIR / cfg.pipeline["run"]["root"]
    runs_root.mkdir(parents=True, exist_ok=True)
    run_id, resumed = _resolve_run_id(cfg, os.environ)
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_run_config(run_dir)   # write the per-run snapshot
    print(f"[driver] run_id={run_id}  resumed={resumed}  mode={cfg.mode}")
    return run_pipeline(cfg, run_dir=run_dir, run_id=run_id, resumed=resumed)


def load_run_config_path() -> Config:
    """Load configs without writing a snapshot yet — we don't know the
    run_dir until after run-id resolution."""
    from agent_runtime.config import load_config
    return load_config()


if __name__ == "__main__":
    raise SystemExit(main())
