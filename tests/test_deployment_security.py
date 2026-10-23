import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION_SHA = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def test_api_image_has_a_minimal_non_root_runtime():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert dockerfile.startswith("FROM python:3.12.12-slim-bookworm\n")
    assert "COPY . ." not in dockerfile
    assert "USER riskqueue" in dockerfile
    assert dockerfile.index("useradd") < dockerfile.index("USER riskqueue")
    assert "PYTHONDONTWRITEBYTECODE=1" in dockerfile
    assert "PIP_NO_CACHE_DIR=1" in dockerfile

    worker = (ROOT / "Dockerfile.worker").read_text()
    assert worker.startswith("FROM python:3.12.12-slim-bookworm\n")
    assert "USER riskqueue" in worker
    assert "PYTHONDONTWRITEBYTECODE=1" in worker
    assert "PIP_NO_CACHE_DIR=1" in worker


def test_docker_context_excludes_secrets_and_development_material():
    ignored = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert {
        ".git",
        ".env*",
        ".venv",
        "tests",
        "docs",
        "infra",
        "node_modules",
        "data",
        "artifacts",
    } <= ignored


def test_compose_mounts_dashboard_sources_excluded_from_api_image():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    volumes = set(compose["services"]["dashboard"].get("volumes", []))
    assert "./dashboard:/app/dashboard:ro" in volumes
    assert "./artifacts/figures:/app/artifacts/figures:ro" in volumes


def test_workflows_pin_actions_and_use_least_privilege():
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        source = path.read_text()
        workflow = yaml.safe_load(source)
        assert workflow.get("permissions") == {"contents": "read"}
        assert all("timeout-minutes" in job for job in workflow["jobs"].values())
        action_references = re.findall(r"uses:\s*([^\s#]+)", source)
        assert action_references
        assert all(ACTION_SHA.fullmatch(reference) for reference in action_references)


def test_ci_installs_locked_dependencies_without_package_scripts():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    website = (ROOT / ".github/workflows/website.yml").read_text()
    assert "uv sync --frozen --extra dev" in ci
    assert "pip install" not in ci
    assert "npm ci --ignore-scripts" in website
    assert "npx --no-install playwright install" in website
