"""Trinity dispatcher and completion helpers.

This module implements a small, file-backed routing layer for OpenClaw/Telegram
task envelopes. It reads the routing policy from ``.forge/routing/*.yaml``,
computes a target node/agent, shells out to the canonical FORGE CLI, and
tracks completion via ``.forge/results/<task_id>.json``.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error as url_error
from urllib import request as url_request

import yaml

HEAVY_WORKLOADS = {"go-test", "coverage-waves", "heavy-builds", "ml-inference"}
LIGHT_WORKLOADS = {"docs", "small-edits", "dispatch", "api-calls"}
DEFAULT_RESULTS_DIR = Path(".forge/results")
DEFAULT_INBOX_DIR = Path(".forge/inbox/trinity")
TRACKING_FILE = Path(".forge/routing/trinity-tracking.jsonl")


@dataclass(slots=True)
class NormalizedEnvelope:
    """Envelope after defaults, validation, and workload inference."""

    task_id: str
    request_id: str
    objective: str
    weight: str
    workload: str
    constraints: dict[str, Any]
    preferred_executor: str | None
    suggested_node: str | None
    approval_required: bool
    approval_prompt: str | None
    artifacts_expected: list[dict[str, Any]]
    raw: dict[str, Any]


@dataclass(slots=True)
class RouteDecision:
    """Resolved node/agent route for a task."""

    task_id: str
    request_id: str
    node: str
    agent: str
    workload: str
    dispatch_target: str
    dispatch_method: str
    rationale: list[str]
    dispatch_file: str
    dispatch_message: str


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)


def current_node() -> str:
    """Resolve the current node identity."""
    node = os.environ.get("FORGE_NODE_ID") or os.environ.get("NODE_ID")
    if node:
        return node.strip().lower()
    return socket.gethostname().split(".")[0].strip().lower()


def default_api_base() -> str:
    """Resolve the default OpenClaw/v3 API base URL."""
    base = os.environ.get("FORGE_API_URL") or os.environ.get("COMMAND_CENTER_URL")
    if base:
        return base.rstrip("/")
    return "http://localhost:8081"


def _resolve_routing_dir(forge_root: Path) -> Path:
    """Return the active routing config directory.

    Lookup order:
    1. ``$FORGE_ROUTING_DIR`` — if set, use that directory directly.
    2. ``config/routing/`` relative to *forge_root* — new canonical location.
    3. ``.forge/routing/`` relative to *forge_root* — legacy fallback (emits a
       deprecation warning to stderr).
    """
    import sys

    env_dir = os.environ.get("FORGE_ROUTING_DIR")
    if env_dir:
        return Path(env_dir)

    canonical = forge_root / "config" / "routing"
    if canonical.is_dir():
        return canonical

    legacy = forge_root / ".forge" / "routing"
    if legacy.is_dir():
        print(
            "Warning: using legacy routing path .forge/routing/ — move to config/routing/",
            file=sys.stderr,
        )
        return legacy

    # Neither exists yet — return the canonical path so callers get a clear
    # FileNotFoundError that points at the right place.
    return canonical


def load_routing_bundle(forge_root: Path) -> dict[str, Any]:
    """Load routing YAML files from the active routing config directory."""
    routing_dir = _resolve_routing_dir(forge_root)
    files = {
        "node_registry": routing_dir / "node-registry.yaml",
        "agent_registry": routing_dir / "agent-registry.yaml",
        "task_envelope": routing_dir / "task-envelope-v1.yaml",
    }
    loaded: dict[str, Any] = {}
    for key, path in files.items():
        loaded[key] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded


def infer_workload(objective: str, weight: str) -> str:
    """Infer a routing workload from free-form objective text."""
    text = objective.lower()
    if "coverage" in text:
        return "coverage-waves"
    if "go test" in text or "pytest" in text or "test suite" in text or "regression" in text:
        return "go-test"
    if "xcode" in text or "ios" in text or "simulator" in text:
        return "ios-builds"
    if "build" in text or "compile" in text or "benchmark" in text:
        return "heavy-builds" if weight == "heavy" else "small-edits"
    if "research" in text or "audit" in text or "review" in text:
        return "research"
    if "docs" in text or "readme" in text or "runbook" in text:
        return "docs"
    if "dispatch" in text or "route" in text or "telegram" in text or "openclaw" in text:
        return "dispatch"
    if "endpoint" in text or "api" in text or "http" in text:
        return "api-calls" if weight == "light" else "heavy-builds"
    return "heavy-builds" if weight == "heavy" else "small-edits"


def normalize_envelope(payload: dict[str, Any], source_name: str = "inline") -> NormalizedEnvelope:
    """Validate and normalize a Task Envelope v1 payload."""
    objective = str(payload.get("objective", "")).strip()
    if not objective:
        raise ValueError("objective is required")

    weight = str(payload.get("weight") or "light").strip().lower()
    if weight not in {"light", "heavy"}:
        raise ValueError("weight must be 'light' or 'heavy'")

    constraints = payload.get("constraints") or {}
    if not isinstance(constraints, dict):
        raise ValueError("constraints must be an object")

    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        task_id = f"TRINITY-{utc_now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

    request_id = str(payload.get("request_id") or "").strip()
    if not request_id:
        request_id = f"{source_name}:{task_id}"

    workload = str(payload.get("workload") or "").strip().lower()
    if not workload:
        workload = infer_workload(objective, weight)

    artifacts_expected = payload.get("artifacts_expected") or []
    if not isinstance(artifacts_expected, list):
        raise ValueError("artifacts_expected must be a list")

    return NormalizedEnvelope(
        task_id=task_id,
        request_id=request_id,
        objective=objective,
        weight=weight,
        workload=workload,
        constraints=constraints,
        preferred_executor=_normalise_optional_str(payload.get("preferred_executor"), lower=True),
        suggested_node=_normalise_optional_str(payload.get("suggested_node"), lower=True),
        approval_required=bool(payload.get("approval_required", False)),
        approval_prompt=_normalise_optional_str(payload.get("approval_prompt")),
        artifacts_expected=[item for item in artifacts_expected if isinstance(item, dict)],
        raw=payload,
    )


def _normalise_optional_str(value: Any, *, lower: bool = False) -> str | None:
    text = str(value).strip() if value is not None else ""
    if lower:
        text = text.lower()
    return text or None


def node_allows_workload(node_name: str, node_cfg: dict[str, Any], workload: str) -> tuple[bool, str | None]:
    """Return whether a node can run the inferred workload."""
    forbidden = {str(item).strip() for item in node_cfg.get("forbidden_workloads", [])}
    allowed = {str(item).strip() for item in node_cfg.get("allowed_workloads", [])}

    if workload in forbidden:
        return False, f"{node_name} forbids workload {workload}"

    if workload in HEAVY_WORKLOADS and node_name == "prya":
        return False, "prya is orchestrator-only for heavy workloads"

    if allowed and workload not in allowed and workload in LIGHT_WORKLOADS.union(HEAVY_WORKLOADS).union({"ios-builds", "research"}):
        return False, f"{node_name} does not advertise workload {workload}"

    return True, None


def choose_route(envelope: NormalizedEnvelope, routing_bundle: dict[str, Any]) -> RouteDecision:
    """Resolve the best route given routing policies and hard guardrails."""
    nodes = ((routing_bundle.get("node_registry") or {}).get("nodes") or {})
    agents = ((routing_bundle.get("agent_registry") or {}).get("agents") or {})
    if not nodes or not agents:
        raise ValueError("routing registries are empty")

    rationale: list[str] = [f"workload={envelope.workload}", f"weight={envelope.weight}"]
    forbidden_nodes = {
        str(node).strip().lower()
        for node in envelope.constraints.get("forbidden_nodes", []) or []
        if str(node).strip()
    }
    required_node = _normalise_optional_str(envelope.constraints.get("required_node"), lower=True)
    max_ram_gb = envelope.constraints.get("max_ram_gb")

    allowed_nodes: dict[str, dict[str, Any]] = {}
    for node_name, node_cfg in nodes.items():
        lowered = str(node_name).strip().lower()
        if lowered in forbidden_nodes:
            continue
        if required_node and lowered != required_node:
            continue
        if max_ram_gb is not None and float(node_cfg.get("ram_gb", 0)) > float(max_ram_gb):
            continue
        allowed, reason = node_allows_workload(lowered, node_cfg, envelope.workload)
        if allowed:
            allowed_nodes[lowered] = node_cfg
        elif reason:
            rationale.append(f"skip node {lowered}: {reason}")

    if envelope.weight == "heavy" and "prya" in allowed_nodes:
        allowed_nodes.pop("prya", None)
        rationale.append("deny prya for heavy work")

    preferred_node_order = preferred_nodes_for_workload(envelope.workload, envelope.suggested_node)
    candidate_agents: list[tuple[int, str, str]] = []

    for agent_name, agent_cfg in agents.items():
        status = str(agent_cfg.get("status", "OK")).upper()
        if status in {"OFFLINE", "COOLDOWN"}:
            continue
        home_node = str(agent_cfg.get("home_node", "")).strip().lower()
        if home_node not in allowed_nodes:
            continue
        forbidden_agent_nodes = {
            str(node).strip().lower()
            for node in agent_cfg.get("forbidden_nodes", []) or []
            if str(node).strip()
        }
        if home_node in forbidden_agent_nodes:
            continue
        if envelope.preferred_executor and agent_name != envelope.preferred_executor:
            continue
        score = score_agent(agent_name, agent_cfg, envelope, preferred_node_order)
        candidate_agents.append((score, home_node, agent_name))

    if not candidate_agents and envelope.preferred_executor:
        rationale.append(f"preferred executor {envelope.preferred_executor} unavailable, falling back")
        fallback_envelope = NormalizedEnvelope(**{**asdict(envelope), "preferred_executor": None})
        return choose_route(fallback_envelope, routing_bundle)

    if not candidate_agents:
        raise ValueError("no eligible agent found for the envelope constraints")

    candidate_agents.sort(reverse=True)
    _, node, agent = candidate_agents[0]
    if envelope.preferred_executor:
        rationale.append(f"preferred executor {agent} accepted")
    rationale.append(f"selected node={node} agent={agent}")

    return RouteDecision(
        task_id=envelope.task_id,
        request_id=envelope.request_id,
        node=node,
        agent=agent,
        workload=envelope.workload,
        dispatch_target=f"forge:{agent}",
        dispatch_method="local_dispatch" if node == current_node() else "cross_node_lead",
        rationale=rationale,
        dispatch_file="",
        dispatch_message="",
    )


def preferred_nodes_for_workload(workload: str, suggested_node: str | None) -> list[str]:
    """Return node preference order for a workload."""
    ordered: list[str] = []
    if workload in HEAVY_WORKLOADS:
        ordered.extend(["sati", "nova"])
    elif workload == "ios-builds":
        ordered.extend(["nova", "gaea"])
    elif workload in {"docs", "small-edits", "dispatch"}:
        ordered.extend(["prya", "vega", "gaea", "nova", "sati"])
    elif workload == "research":
        ordered.extend(["nova", "sati", "prya"])
    else:
        ordered.extend(["prya", "nova", "sati", "vega", "gaea"])
    if suggested_node and suggested_node not in ordered:
        ordered.insert(0, suggested_node)
    elif suggested_node:
        ordered.remove(suggested_node)
        ordered.insert(0, suggested_node)
    return ordered


def score_agent(
    agent_name: str,
    agent_cfg: dict[str, Any],
    envelope: NormalizedEnvelope,
    preferred_node_order: list[str],
) -> int:
    """Score an agent for route selection."""
    score = 0
    home_node = str(agent_cfg.get("home_node", "")).strip().lower()
    if home_node in preferred_node_order:
        score += (len(preferred_node_order) - preferred_node_order.index(home_node)) * 10

    best_at = {str(item).strip() for item in agent_cfg.get("best_at", [])}
    avoid_for = {str(item).strip() for item in agent_cfg.get("avoid_for", [])}
    if envelope.workload in best_at:
        score += 50
    if envelope.workload in avoid_for:
        score -= 100

    objective = envelope.objective.lower()
    if "review" in objective or "audit" in objective:
        if agent_name == "gemini":
            score += 20
    if envelope.weight == "heavy" and home_node in {"sati", "nova"}:
        score += 15
    if envelope.weight == "light" and home_node == "prya":
        score += 8

    status = str(agent_cfg.get("status", "OK")).upper()
    if status == "CAUTION":
        score -= 10
    if agent_cfg.get("cost_class") == "expensive":
        score -= 5
    if envelope.preferred_executor == agent_name:
        score += 1_000
    return score


def ensure_dispatch_file(
    forge_root: Path,
    envelope: NormalizedEnvelope,
    route: RouteDecision,
) -> tuple[str, str]:
    """Create the dispatch file and the message that references it."""
    dispatch_dir = forge_root / ".forge" / "dispatches"
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    dispatch_name = f"trinity-{envelope.task_id}.md"
    dispatch_path = dispatch_dir / dispatch_name
    results_path = DEFAULT_RESULTS_DIR / f"{envelope.task_id}.json"
    dispatch_path.write_text(
        _render_dispatch_file(envelope, route, results_path),
        encoding="utf-8",
    )
    message = f"Task: .forge/dispatches/{dispatch_name}"
    return str(dispatch_path.relative_to(forge_root)), message


def _render_dispatch_file(
    envelope: NormalizedEnvelope,
    route: RouteDecision,
    result_path: Path,
) -> str:
    artifacts = envelope.artifacts_expected or []
    artifact_lines = "\n".join(
        f"- {json.dumps(item, sort_keys=True)}" for item in artifacts
    ) or "- none declared"
    approval_block = ""
    if envelope.approval_required:
        approval_block = (
            "\nApproval requirement:\n"
            f"- approval_required: true\n"
            f"- approval_prompt: {envelope.approval_prompt or '(none provided)'}\n"
        )

    return (
        f"# Trinity Task {envelope.task_id}\n\n"
        f"Objective: {envelope.objective}\n"
        f"Route: node={route.node} agent={route.agent} workload={route.workload}\n"
        f"Weight: {envelope.weight}\n"
        f"Request ID: {envelope.request_id}\n"
        f"{approval_block}\n"
        "Completion contract:\n"
        f"- Write `{result_path.as_posix()}` with fields `task_id,status,agent,node,summary,result_path,artifacts`, optional `approval`, and `completed_at`.\n"
        "- Immediately POST the exact same JSON payload to `/api/openclaw/ingest`.\n"
        "- Keep `result_path` set to the `.forge/results/<task_id>.json` file you wrote.\n\n"
        "Expected artifacts:\n"
        f"{artifact_lines}\n"
    )


def append_tracking_record(forge_root: Path, record: dict[str, Any]) -> Path:
    """Append a tracking record to the Trinity tracking log."""
    tracking_path = forge_root / TRACKING_FILE
    tracking_path.parent.mkdir(parents=True, exist_ok=True)
    with tracking_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return tracking_path


def dispatch_route(
    forge_root: Path,
    envelope: NormalizedEnvelope,
    route: RouteDecision,
    *,
    perform_dispatch: bool = True,
) -> dict[str, Any]:
    """Persist route artifacts and optionally invoke the FORGE CLI."""
    dispatch_file, dispatch_message = ensure_dispatch_file(forge_root, envelope, route)
    route.dispatch_file = dispatch_file
    route.dispatch_message = dispatch_message

    tracking_record = {
        "recorded_at": utc_now().isoformat(),
        "request_id": route.request_id,
        "task_id": route.task_id,
        "objective": envelope.objective,
        "node": route.node,
        "agent": route.agent,
        "workload": route.workload,
        "dispatch_target": route.dispatch_target,
        "dispatch_method": route.dispatch_method,
        "dispatch_file": route.dispatch_file,
        "approval_required": envelope.approval_required,
    }
    tracking_path = append_tracking_record(forge_root, tracking_record)

    result = {
        "task_id": route.task_id,
        "request_id": route.request_id,
        "node": route.node,
        "agent": route.agent,
        "dispatch_file": route.dispatch_file,
        "dispatch_message": route.dispatch_message,
        "dispatch_method": route.dispatch_method,
        "tracking_file": str(tracking_path.relative_to(forge_root)),
        "rationale": route.rationale,
        "dispatched": False,
    }

    if not perform_dispatch:
        return result

    if route.node == current_node():
        command = ["forge", "dispatch", "send", route.dispatch_target, route.dispatch_message]
    else:
        summary = f"Trinity route {route.task_id} -> {route.dispatch_target} ({route.dispatch_file})"
        command = [
            "forge",
            "lead",
            "send",
            "--to-node",
            route.node,
            "--task-id",
            route.task_id,
            "--summary",
            summary,
            "--strict",
        ]

    completed = subprocess.run(
        command,
        cwd=str(forge_root),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    result["dispatched"] = completed.returncode == 0
    result["command"] = command
    result["stdout"] = completed.stdout.strip()
    result["stderr"] = completed.stderr.strip()
    result["returncode"] = completed.returncode
    return result


def read_envelope_file(path: Path) -> dict[str, Any]:
    """Load a YAML or JSON envelope from disk."""
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(raw)
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("envelope must decode to an object")
    return data


def ingest_completion_payload(
    payload: dict[str, Any],
    *,
    api_base: str | None = None,
    token: str | None = None,
    timeout_seconds: float = 10.0,
) -> tuple[dict[str, Any] | None, str | None]:
    """POST a completion payload to ``/api/openclaw/ingest``."""
    base = (api_base or default_api_base()).rstrip("/")
    endpoint = f"{base}/api/openclaw/ingest"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    resolved_token = token or os.environ.get("FORGE_API_TOKEN") or os.environ.get("FORGE_WEBHOOK_TOKEN")
    if resolved_token:
        headers["Authorization"] = f"Bearer {resolved_token}"

    request = url_request.Request(
        url=endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with url_request.urlopen(request, timeout=max(timeout_seconds, 1.0)) as response:
            body = response.read().decode("utf-8").strip()
    except url_error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore").strip()
        return None, f"HTTP {exc.code}: {raw or str(exc)}"
    except (url_error.URLError, TimeoutError, OSError, ValueError) as exc:
        return None, str(exc)

    if not body:
        return {}, None
    return json.loads(body), None


def load_result_payload(result_file: Path) -> dict[str, Any]:
    """Read and validate a result payload from disk."""
    payload = json.loads(result_file.read_text(encoding="utf-8"))
    for required in ("task_id", "status", "agent", "node", "summary", "result_path", "artifacts"):
        if required not in payload:
            raise ValueError(f"result payload missing field: {required}")
    return payload


def watch_result_file(
    forge_root: Path,
    *,
    task_id: str,
    api_base: str | None = None,
    poll_interval: float = 5.0,
    push_grace_seconds: float = 300.0,
    stale_after_seconds: float = 600.0,
) -> dict[str, Any]:
    """Wait for a result file, then push it to OpenClaw ingest.

    The 5-minute and 10-minute timers match the Trinity fallback contract:
    prefer immediate push, poll for the durable result file after 5 minutes,
    and alert ``prya-lead`` after 10 minutes if nothing arrives.
    """
    result_file = forge_root / DEFAULT_RESULTS_DIR / f"{task_id}.json"
    started = time.monotonic()
    warned_poll = False
    while True:
        elapsed = time.monotonic() - started
        if result_file.exists():
            payload = load_result_payload(result_file)
            response, error = ingest_completion_payload(payload, api_base=api_base)
            return {
                "task_id": task_id,
                "result_file": str(result_file.relative_to(forge_root)),
                "payload": payload,
                "response": response,
                "push_error": error,
                "elapsed_seconds": round(elapsed, 2),
            }
        if elapsed >= stale_after_seconds:
            text = (
                f"Trinity stale task {task_id}: no push and no "
                f".forge/results/{task_id}.json after {int(stale_after_seconds)}s. Alert prya-lead."
            )
            emit_system_event(text)
            raise TimeoutError(text)
        if elapsed >= push_grace_seconds and not warned_poll:
            warned_poll = True
        time.sleep(max(poll_interval, 0.25))


def emit_system_event(text: str) -> subprocess.CompletedProcess[str] | None:
    """Best-effort OpenClaw system event emission."""
    try:
        return subprocess.run(
            ["openclaw", "system", "event", "--text", text, "--mode", "now"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
