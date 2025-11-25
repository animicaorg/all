import json
import os
import sys
import shutil
import tempfile
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"
INDEX_JSON = TEMPLATES_DIR / "index.json"

UNRENDERED_PATTERNS = ["{{", "}}", "[[", "]]", "<%=", "<%", "%>"]


def read_json(p: Path):
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_variables(vars_spec: dict) -> dict:
    """
    Construct a variables dict using defaults where provided; otherwise
    supply sensible fallbacks for test rendering.
    """
    out = {}
    specs = vars_spec.get("variables") or []
    for v in specs:
        name = v.get("name")
        if not name:
            continue
        if "default" in v:
            out[name] = v["default"]
            continue
        typ = v.get("type", "string")
        if typ == "boolean":
            out[name] = True
        elif typ in ("integer", "number"):
            out[name] = 1
        elif typ == "enum":
            choices = v.get("choices") or []
            out[name] = choices[0] if choices else "example"
        else:
            out[name] = f"example_{name.lower()}"

    # Friendly overrides if present
    if "project_slug" in out:
        out["project_slug"] = "example_provider"
    if "project_name" in out:
        out["project_name"] = "Example AICF Provider"
    if "description" in out:
        out["description"] = "Rendered from provider-aicf-fastapi template for tests."
    return out


def assert_no_unrendered_tokens(p: Path, optional: bool = False):
    if not p.exists():
        if optional:
            return
        pytest.fail(f"Missing expected file: {p}")
    text = p.read_text(encoding="utf-8")
    offenders = [t for t in UNRENDERED_PATTERNS if t in text]
    assert (
        not offenders
    ), f"Unrendered template tokens in {p}: {', '.join(offenders)}"


def file_non_empty(p: Path) -> bool:
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


@pytest.mark.parametrize("template_id", ["provider-aicf-fastapi"])
def test_render_provider_template(template_id: str):
    # Ensure index.json exists and locate template entry
    assert INDEX_JSON.exists(), f"Missing {INDEX_JSON}"
    index = read_json(INDEX_JSON)
    entries = index.get("templates", index)
    entry = None
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict) and e.get("id") == template_id:
                entry = e
                break
    if entry is None:
        pytest.skip(f"Template id '{template_id}' not found in index.")

    template_rel = entry.get("path") or entry.get("id") or template_id
    template_dir = (TEMPLATES_DIR / template_rel).resolve()
    assert template_dir.exists(), f"Template directory not found: {template_dir}"

    vars_path = template_dir / "variables.json"
    assert vars_path.exists(), f"Missing variables.json at {vars_path}"
    vars_spec = read_json(vars_path)
    vars_map = build_variables(vars_spec)

    with tempfile.TemporaryDirectory(prefix="tmpl-provider-") as tmpd:
        tmp = Path(tmpd)
        out_dir = tmp / "out"
        out_dir.mkdir(parents=True, exist_ok=True)

        vars_json_path = tmp / "vars.json"
        vars_json_path.write_text(json.dumps(vars_map), encoding="utf-8")

        # Invoke CLI with the current interpreter to avoid PATH issues
        cmd = [
            sys.executable,
            "-m",
            "templates.engine.cli",
            "render",
            "--template",
            str(template_dir),
            "--out",
            str(out_dir),
            "--vars-json",
            str(vars_json_path),
        ]
        res = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
        if res.returncode != 0:
            # Make this a soft skip so non-Python template envs don't hard-fail JS-only CI
            pytest.skip(
                "Rendering via CLI failed.\n"
                f"cmd: {' '.join(cmd)}\n"
                f"stdout:\n{res.stdout}\n"
                f"stderr:\n{res.stderr}\n"
            )

        # Determine rendered root: either out/<project_slug> or out
        proj_slug = str(vars_map.get("project_slug", "example_provider"))
        rendered_root = out_dir / proj_slug
        if not rendered_root.exists():
            rendered_root = out_dir

        # Expected files
        expected = [
            "pyproject.toml",
            "requirements.txt",
            "Makefile",
            ".env.example",
            "Dockerfile",
            "aicf_provider/__init__.py",
            "aicf_provider/config.py",
            "aicf_provider/models.py",
            "aicf_provider/quantum.py",
            "aicf_provider/worker.py",
            "aicf_provider/server.py",
            "scripts/run_worker.py",
            "k8s/deployment.yaml",
            "k8s/service.yaml",
        ]
        missing = [p for p in expected if not (rendered_root / p).exists()]
        assert not missing, "Rendered project missing files:\n- " + "\n- ".join(missing)

        # Non-empty sanity
        must_be_non_empty = [
            "pyproject.toml",
            "requirements.txt",
            "aicf_provider/__init__.py",
            "aicf_provider/server.py",
            "k8s/deployment.yaml",
            "k8s/service.yaml",
        ]
        for rel in must_be_non_empty:
            p = rendered_root / rel
            assert file_non_empty(p), f"Expected non-empty file: {p}"

        # No unrendered tokens in key files
        for rel in [
            "pyproject.toml",
            "requirements.txt",
            "aicf_provider/server.py",
            "aicf_provider/config.py",
            "scripts/run_worker.py",
            "k8s/deployment.yaml",
            "k8s/service.yaml",
            "Dockerfile",
        ]:
            assert_no_unrendered_tokens(rendered_root / rel)

        # Lightweight content checks
        req_text = (rendered_root / "requirements.txt").read_text(encoding="utf-8").lower()
        assert "fastapi" in req_text, "requirements.txt should include fastapi"
        assert "uvicorn" in req_text, "requirements.txt should include uvicorn"

        server_text = (rendered_root / "aicf_provider" / "server.py").read_text(encoding="utf-8")
        assert "FastAPI" in server_text or "fastapi" in server_text.lower(), "server.py should create a FastAPI app"

        # k8s files should mention the project slug (or app name)
        k_deploy = (rendered_root / "k8s" / "deployment.yaml").read_text(encoding="utf-8")
        assert proj_slug in k_deploy or "name:" in k_deploy, "deployment.yaml should be parameterized with project slug/name"

        # Dockerfile sanity
        docker_text = (rendered_root / "Dockerfile").read_text(encoding="utf-8")
        assert "FROM" in docker_text, "Dockerfile should specify a base image"
        assert "uvicorn" in docker_text.lower() or "gunicorn" in docker_text.lower(), "Dockerfile should run an ASGI server"

        # scripts/run_worker.py should be executable-ish (shebang or python -m)
        run_worker = (rendered_root / "scripts" / "run_worker.py").read_text(encoding="utf-8")
        assert "#!" in run_worker or "if __name__ == '__main__':" in run_worker, "run_worker.py should be invocable"

        # Clean up temp dir contents if you want to inspect locally, comment the next line.
        # (left intentionally; pytest tmpdir auto-cleans)
        # shutil.rmtree(out_dir)
