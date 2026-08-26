from __future__ import annotations

import argparse
import asyncio
import json
import math
import tempfile
import time
from pathlib import Path
from typing import Any

from pydantic_ai.models.function import DeltaToolCall, FunctionModel

import app.database as database_module
import app.knowledge as knowledge_module
from app.chat import ChatOrchestrator
from app.database import connect, init_database
from app.knowledge import KnowledgeService
from app.providers import ProviderSnapshot


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return ordered[index]


def configure_isolated_storage(root: Path) -> None:
    data = root / "data"
    knowledge = data / "knowledge"
    assets = data / "knowledge_assets"
    knowledge.mkdir(parents=True)
    assets.mkdir(parents=True)
    database_module.DATABASE_PATH = data / "app.db"
    knowledge_module.KNOWLEDGE_DIR = knowledge
    knowledge_module.KNOWLEDGE_ASSET_DIR = assets
    knowledge_module.LEGACY_UPLOAD_DIR = data / "uploads"
    init_database()


def evaluate_retrieval(service: KnowledgeService, repetitions: int) -> dict[str, Any]:
    corpus = json.loads((FIXTURES / "knowledge_retrieval_eval.json").read_text(encoding="utf-8"))
    for document in corpus["documents"]:
        service.ingest(document["filename"], document["content"].encode("utf-8"))

    positive_cases = [
        *({**case, "split": "smoke", "category": "smoke"} for case in corpus["queries"]),
        *({**case, "split": "challenge"} for case in corpus["challenge_queries"]),
    ]
    outcomes: list[dict[str, Any]] = []
    for case in positive_cases:
        results = service.search(case["query"], 3)
        names = [item["display_name"] for item in results]
        outcomes.append({
            "split": case["split"],
            "category": case["category"],
            "query": case["query"],
            "expected": case["expected"],
            "rank": names.index(case["expected"]) + 1 if case["expected"] in names else None,
            "retrieved": names,
        })

    def rank_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
        count = len(items)
        return {
            "queries": count,
            "recall_at_1": sum(item["rank"] == 1 for item in items) / count,
            "recall_at_3": sum(item["rank"] is not None for item in items) / count,
            "mrr_at_3": sum(1 / item["rank"] if item["rank"] else 0 for item in items) / count,
        }

    smoke_outcomes = [item for item in outcomes if item["split"] == "smoke"]
    challenge_outcomes = [item for item in outcomes if item["split"] == "challenge"]
    categories = sorted({item["category"] for item in challenge_outcomes})
    category_metrics = {
        category: rank_metrics([item for item in challenge_outcomes if item["category"] == category])
        for category in categories
    }

    easy_negative_outcomes = []
    for query in corpus["negative_queries"]:
        names = [item["display_name"] for item in service.search(query, 3)]
        easy_negative_outcomes.append({"query": query, "rejected": not names, "retrieved": names})
    hard_negative_outcomes = []
    for case in corpus["hard_negative_queries"]:
        names = [item["display_name"] for item in service.search(case["query"], 3)]
        hard_negative_outcomes.append({
            "category": case["category"],
            "query": case["query"],
            "rejected": not names,
            "retrieved": names,
        })

    latency_samples: list[float] = []
    for _ in range(repetitions):
        for case in positive_cases:
            started = time.perf_counter()
            service.search(case["query"], 3)
            latency_samples.append((time.perf_counter() - started) * 1000)

    return {
        "dataset": corpus["dataset"],
        "documents": len(corpus["documents"]),
        "overall": rank_metrics(outcomes),
        "smoke": rank_metrics(smoke_outcomes),
        "challenge": rank_metrics(challenge_outcomes),
        "challenge_by_category": category_metrics,
        "negative_queries": {
            "easy": {
                "queries": len(easy_negative_outcomes),
                "rejection_rate": sum(item["rejected"] for item in easy_negative_outcomes) / len(easy_negative_outcomes),
            },
            "hard": {
                "queries": len(hard_negative_outcomes),
                "rejection_rate": sum(item["rejected"] for item in hard_negative_outcomes) / len(hard_negative_outcomes),
            },
        },
        "latency_ms": {
            "samples": len(latency_samples),
            "p50": percentile(latency_samples, 0.50),
            "p95": percentile(latency_samples, 0.95),
        },
        "positive_failures": [item for item in outcomes if item["rank"] != 1],
        "hard_negative_false_positives": [item for item in hard_negative_outcomes if not item["rejected"]],
    }


class StubGateway:
    def __init__(self, model: FunctionModel) -> None:
        self.model = model

    def snapshot(self) -> ProviderSnapshot:
        return ProviderSnapshot("offline", "function-model", "unused")

    def build_model(self, _: ProviderSnapshot) -> FunctionModel:
        return self.model


class StubKnowledge:
    def search(self, _: str, limit: int = 6) -> list[dict]:
        return [{
            "document_id": "eval-doc",
            "display_name": "公司介绍.md",
            "content": "公司主要提供云小智客服平台和企业知识库服务。",
            "section": "主要产品",
            "location": "主要产品",
            "score": -1.0,
            "images": [],
        }][:limit]


def parse_sse(raw: str) -> dict[str, Any]:
    event_line, data_line = raw.strip().split("\n", 1)
    return {
        "event": event_line.removeprefix("event: "),
        "data": json.loads(data_line.removeprefix("data: ")),
    }


def visible_answer(frames: list[dict[str, Any]]) -> str:
    answer = ""
    for frame in frames:
        if frame["event"] == "answer_reset":
            answer = ""
        elif frame["event"] == "token":
            answer += frame["data"]["text"]
    return answer


async def evaluate_agent() -> dict[str, Any]:
    fixture = json.loads((FIXTURES / "agent_orchestration_eval.json").read_text(encoding="utf-8"))
    outcomes: list[dict[str, Any]] = []
    latencies: list[float] = []

    for index, case in enumerate(fixture["cases"]):
        calls = 0

        async def stream_model(_, __, scenario=case):
            nonlocal calls
            calls += 1
            if scenario["tool"] and calls == 1:
                yield {0: DeltaToolCall(
                    name=scenario["tool"],
                    json_args=json.dumps(scenario["arguments"], ensure_ascii=False),
                    tool_call_id=f"eval-{index}",
                )}
            else:
                yield scenario["answer"]

        orchestrator = ChatOrchestrator(
            StubGateway(FunctionModel(stream_function=stream_model)),
            StubKnowledge(),
        )
        session_id = orchestrator.create_session()["id"]
        frames: list[dict[str, Any]] = []
        started = time.perf_counter()
        async for raw in orchestrator.stream(session_id, case["prompt"]):
            frames.append(parse_sse(raw))
        latencies.append((time.perf_counter() - started) * 1000)

        events = [frame["event"] for frame in frames]
        tools = [frame["data"]["tool"] for frame in frames if frame["event"] == "tool_start"]
        passed = (
            events[-1:] == ["done"]
            and visible_answer(frames) == case["answer"]
            and tools == ([case["tool"]] if case["tool"] else [])
            and (not case.get("expects_citation") or "citation" in events)
            and (not case.get("expects_contact_form") or "contact_form" in events)
        )
        with connect() as db:
            run = db.execute(
                "SELECT status,error_code FROM agent_runs WHERE session_id=? ORDER BY started_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        passed = passed and bool(run) and run["status"] == "complete" and run["error_code"] is None
        outcomes.append({"name": case["name"], "passed": passed, "events": events, "tools": tools})

    tool_cases = [item for item, case in zip(outcomes, fixture["cases"]) if case["tool"]]
    return {
        "dataset": "scripted_orchestration_v1",
        "scope": "Deterministic end-to-end orchestration; does not measure LLM tool-selection intelligence.",
        "cases": len(outcomes),
        "workflow_conformance_rate": sum(item["passed"] for item in outcomes) / len(outcomes),
        "tool_execution_success_rate": sum(item["passed"] for item in tool_cases) / len(tool_cases),
        "latency_ms": {
            "samples": len(latencies),
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
        },
        "outcomes": outcomes,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run isolated knowledge retrieval and Agent orchestration evaluations.")
    parser.add_argument("--repetitions", type=int, default=30, help="Retrieval latency repetitions per positive query.")
    args = parser.parse_args()
    if args.repetitions <= 0:
        parser.error("--repetitions must be greater than zero")

    with tempfile.TemporaryDirectory(prefix="customer-service-eval-") as temp_dir:
        configure_isolated_storage(Path(temp_dir))
        retrieval = evaluate_retrieval(KnowledgeService(), args.repetitions)
        orchestration = await evaluate_agent()
        checks = {
            "smoke_recall_at_3_gte_0_90": retrieval["smoke"]["recall_at_3"] >= 0.90,
            "challenge_recall_at_1_gte_0_70": retrieval["challenge"]["recall_at_1"] >= 0.70,
            "challenge_recall_at_3_gte_0_80": retrieval["challenge"]["recall_at_3"] >= 0.80,
            "easy_negative_rejection_rate_gte_0_30": retrieval["negative_queries"]["easy"]["rejection_rate"] >= 0.30,
            "agent_workflow_conformance_eq_1_00": orchestration["workflow_conformance_rate"] == 1.0,
        }
        report = {
            "evaluation_version": "2.0",
            "storage": "isolated temporary database; production data and API keys are not used",
            "retrieval": retrieval,
            "agent_orchestration": orchestration,
            "quality_gates": {
                "passed": all(checks.values()),
                "checks": checks,
                "note": "Gates are regression floors below the frozen v2 baseline, not target scores.",
            },
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["quality_gates"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
