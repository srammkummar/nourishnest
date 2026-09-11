"""Optional real local-model evaluation; separate from deterministic fake reports."""

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import event

from nourish_nest.api import app
from nourish_nest.assistant_provider import get_chat_provider
from nourish_nest.config import Settings
from nourish_nest.database import get_db
from nourish_nest.evals.fixtures import IDS, NOW, evaluation_database
from nourish_nest.evals.meal_planning import (
    DATASET,
    evaluate_response,
    snapshot,
)
from nourish_nest.evals.meal_planning import (
    VERSION as DATASET_VERSION,
)
from nourish_nest.ollama_provider import PROMPT_VERSION, OllamaChatProvider

VERSION = "ollama-meal-planning-eval-v1"
DEFAULT_OUTPUT = "artifacts/ai-evals/ollama-meal-planning-v1"


class EvaluationProvider(OllamaChatProvider):
    def __init__(self, settings, **kwargs):
        super().__init__(settings, **kwargs)
        self.calls = 0
        self.chat_attempts = 0

    async def interpret(self, messages):
        self.calls += 1
        return await super().interpret(messages)

    def client(self):
        client = super().client()

        async def record(request):
            if request.url.path == "/api/chat":
                self.chat_attempts += 1

        client.event_hooks["request"].append(record)
        return client


def run_case(case, settings, provider_factory):
    engine, sessions = evaluation_database()
    provider = provider_factory(settings)
    overrides = app.dependency_overrides.copy()

    def db():
        with sessions() as session:
            yield session

    statements = []

    def record(_, __, statement, *args):
        if (
            statement.lstrip()
            .upper()
            .startswith(("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP"))
        ):
            statements.append(statement.split()[0])

    try:
        app.dependency_overrides[get_db] = db
        app.dependency_overrides[get_chat_provider] = lambda: provider
        data = {"user_message": case["message"], **case.get("request", {})}
        if case.get("member"):
            data["member_id"] = str(IDS[case["member"]])
        with sessions() as session:
            before = snapshot(session)
        with (
            patch(
                "nourish_nest.assistant_services.get_settings",
                return_value=settings.model_copy(
                    update={"ai_max_tool_calls": case.get("tool_limit", 4)}
                ),
            ),
            patch("nourish_nest.planning_services.utc_now", return_value=NOW),
            patch("nourish_nest.grocery_shortage_services.utc_now", return_value=NOW),
            TestClient(app) as client,
        ):
            event.listen(engine, "before_cursor_execute", record)
            response = client.post(
                f"/v1/households/{IDS[case.get('household', 'household')]}/assistant/meal-plan-preview",
                json=data,
                headers={"x-request-id": case["id"]},
            )
            event.remove(engine, "before_cursor_execute", record)
            with sessions() as session:
                metrics, reasons = evaluate_response(
                    case,
                    response.status_code,
                    response.json(),
                    session,
                    before == snapshot(session),
                    statements,
                    provider.calls,
                )
        return {
            "id": case["id"],
            "passed": not reasons,
            "metrics": metrics,
            "reasons": reasons,
            "http_chat_attempts": provider.chat_attempts,
        }
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)
        engine.dispose()


def run_evaluations(settings, output_dir=None, *, provider_factory=EvaluationProvider):
    if output_dir:
        output = Path(output_dir).resolve()
        fake = Path("artifacts/ai-evals/meal-planning-v1").resolve()
        if output == fake or fake in output.parents:
            raise ValueError("Ollama evaluations cannot overwrite fake-provider reports")
    asyncio.run(provider_factory(settings).probe())
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    if dataset["evaluation_version"] != DATASET_VERSION:
        raise ValueError("Evaluation dataset version mismatch")
    results = []
    for case in dataset["cases"]:
        if "provider" in case:
            continue
        started = perf_counter()
        try:
            result = run_case(case, settings, provider_factory)
        except Exception:  # noqa: BLE001 - never record private exception text
            result = {
                "id": case["id"],
                "passed": False,
                "metrics": {"evaluation_execution": False},
                "reasons": ["Unexpected evaluation execution failure"],
                "http_chat_attempts": None,
            }
        result["latency_ms"] = round((perf_counter() - started) * 1000, 2)
        results.append(result)
    counts = defaultdict(lambda: {"passed": 0, "evaluated": 0})
    for result in results:
        for name, passed in result["metrics"].items():
            counts[name]["passed"] += int(passed)
            counts[name]["evaluated"] += 1
    passed = sum(row["passed"] for row in results)
    report = {
        "evaluation_version": VERSION,
        "dataset_version": DATASET_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": settings.ai_model,
        "scope": "Real local Ollama intent + deterministic orchestration; not fake-provider scores",
        "total_cases": len(results),
        "passed_cases": passed,
        "pass_rate": passed / len(results),
        "excluded_cases": [c["id"] for c in dataset["cases"] if "provider" in c],
        "exclusion_reason": "Synthetic provider faults remain in the unchanged fake suite",
        "latency_ms": {
            "total": sum(r["latency_ms"] for r in results),
            "mean": sum(r["latency_ms"] for r in results) / len(results),
        },
        "metrics": {
            name: {**count, "score": count["passed"] / count["evaluated"]}
            for name, count in sorted(counts.items())
        },
        "failed_cases": [r for r in results if not r["passed"]],
        "cases": results,
    }
    if output_dir:
        output.mkdir(parents=True, exist_ok=True)
        (output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        lines = [
            f"# {VERSION}",
            "",
            report["scope"],
            "",
            f"Model: {settings.ai_model}; prompt: {PROMPT_VERSION}; dataset: {DATASET_VERSION}.",
            f"Passed: {passed}/{len(results)} ({report['pass_rate']:.1%}).",
            f"Mean end-to-end case latency: {report['latency_ms']['mean']:.2f} ms.",
            "",
            "Per-case latency, HTTP chat attempts, and failures: results.json.",
        ]
        lines.extend(f"- {name}: {value['score']:.1%}" for name, value in report["metrics"].items())
        lines.extend(
            f"- Failed {row['id']}: {', '.join(row['reasons'])}" for row in report["failed_cases"]
        )
        (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        report = run_evaluations(Settings(ai_provider="ollama"), args.output_dir)
    except Exception:  # noqa: BLE001 - clean optional command, no configuration/exception secrets
        print(
            "Local evaluation unavailable. Start Ollama with cloud disabled, configure an already "
            "installed APP_AI_MODEL, loopback APP_OLLAMA_BASE_URL and APP_AI_TIMEOUT_SECONDS=60. "
            "No model is installed or downloaded automatically; use a separate Ollama report directory."
        )
        return 0
    print(
        f"{VERSION}: {report['passed_cases']}/{report['total_cases']} passed. "
        f"Real local-model report: {args.output_dir}"
    )
    return 0 if not report["failed_cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
