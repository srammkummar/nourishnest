"""Versioned, offline database-backed retrieval evaluation; exits nonzero on regression."""

import argparse
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from nourish_nest.database import Base
from nourish_nest.knowledge_ingestion import ingest_document
from nourish_nest.knowledge_schemas import DocumentInput, RetrievalRequest
from nourish_nest.knowledge_services import KnowledgeService
from nourish_nest.models import Household, KnowledgeChunk

DATASET = Path(__file__).with_name("knowledge_retrieval_v1.json")
HOUSEHOLDS = {"a": UUID(int=1001), "b": UUID(int=1002)}
THRESHOLDS = {"hit_at_1": Decimal("0.85"), "hit_at_3": Decimal("0.95"),
              "hit_at_5": Decimal("0.95"), "mean_reciprocal_rank": Decimal("0.90"),
              "citation_correctness": Decimal(1), "household_isolation_accuracy": Decimal(1),
              "no_answer_accuracy": Decimal(1), "deterministic_repeat_accuracy": Decimal(1),
              "source_filter_accuracy": Decimal(1)}


def seed(session, dataset):
    session.add_all([Household(id=value, name=f"Evaluation {key}", timezone="UTC", currency="USD")
                     for key, value in HOUSEHOLDS.items()])
    session.flush()
    contents = {d["title"]: d["content"] for d in dataset["documents"] if "content" in d}
    for doc in dataset["documents"]:
        data = DocumentInput(title=doc["title"], source_name="Original evaluation fixture",
                             source_type=doc["source_type"],
                             visibility="household" if doc.get("household") else "global",
                             household_id=HOUSEHOLDS.get(doc.get("household")))
        ingest_document(session, data, doc.get("content") or contents[doc["duplicate_of"]])
    session.commit()


def evaluate_case(session, case):
    household = HOUSEHOLDS[case.get("household", "a")]
    request = RetrievalRequest(query=case["query"], source_types=case.get("source_types"))
    service = KnowledgeService(session)
    response = service.retrieve(household, request)
    repeated = service.retrieve(household, request)
    chunks = {c.id: c for c in session.scalars(select(KnowledgeChunk))}
    titles = [r.document_title for r in response.results]
    relevant_ranks = [r.rank for r in response.results if r.document_title in case["expected"]]
    rank = min(relevant_ranks, default=0)
    citations = all(
        r.chunk_id in chunks and r.excerpt == chunks[r.chunk_id].content
        and r.document_id == chunks[r.chunk_id].document_id
        and r.document_title == chunks[r.chunk_id].document.title
        and r.source_name == chunks[r.chunk_id].document.source_name
        and r.source_uri == chunks[r.chunk_id].document.source_uri
        and r.chunk_index == chunks[r.chunk_id].chunk_index
        and r.heading_path == chunks[r.chunk_id].heading_path
        for r in response.results)
    isolation = all(chunks[r.chunk_id].document.household_id in {None, household}
                    for r in response.results)
    filters = all(chunks[r.chunk_id].document.source_type in case["source_types"]
                  for r in response.results) if case.get("source_types") else True
    extra = (not case.get("ordered") or titles[:len(case["ordered"])] == case["ordered"])
    if "count" in case:
        extra &= len(titles) == case["count"]
    if case.get("warning"):
        extra &= case["warning"] in [w.code for w in response.warnings]
    no_answer = not response.results and "no_relevant_evidence" in [w.code for w in response.warnings]
    return {"id": case["id"], "rank": rank, "answerable": bool(case["expected"]),
            "titles": titles, "citation_correctness": citations,
            "household_isolation_accuracy": isolation, "no_answer_accuracy": no_answer,
            "deterministic_repeat_accuracy": response == repeated,
            "source_filter_accuracy": filters, "filtered": bool(case.get("source_types")),
            "extra_expectations": bool(extra)}


def run_evaluations(output: Path, dataset_path: Path = DATASET):
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            seed(session, dataset)
            results = [evaluate_case(session, case) for case in dataset["cases"]]
    finally:
        engine.dispose()
    answerable = [r for r in results if r["answerable"]]
    metrics, denominators = {}, {}
    for k in (1, 3, 5):
        name = f"hit_at_{k}"
        denominators[name] = len(answerable)
        metrics[name] = Decimal(sum(0 < r["rank"] <= k for r in answerable)) / len(answerable)
    denominators["mean_reciprocal_rank"] = len(answerable)
    metrics["mean_reciprocal_rank"] = sum(
        (Decimal(1) / r["rank"] if r["rank"] else Decimal(0) for r in answerable),
        Decimal(0)) / len(answerable)
    for name in sorted(THRESHOLDS.keys() - metrics.keys()):
        subset = ([r for r in results if not r["answerable"]] if name == "no_answer_accuracy" else
                  [r for r in results if r["filtered"]] if name == "source_filter_accuracy" else results)
        denominators[name] = len(subset)
        metrics[name] = Decimal(sum(r[name] for r in subset)) / len(subset)
    passed = all(metrics[k] >= threshold for k, threshold in THRESHOLDS.items())
    passed &= all(r["extra_expectations"] for r in results)
    report = {"version": dataset["version"], "passed": passed, "case_count": len(results),
              "metrics": {k: str(v) for k, v in sorted(metrics.items())},
              "thresholds": {k: str(v) for k, v in sorted(THRESHOLDS.items())},
              "denominators": denominators, "cases": results}
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    rows = ["# Knowledge retrieval v1 evaluation", "", f"Passed: {passed}; cases: {len(results)}", "",
            "| Metric | Measured | Required | Cases |", "|---|---:|---:|---:|"]
    rows += [f"| {k} | {metrics[k]:.4f} | {THRESHOLDS[k]} | {denominators[k]} |"
             for k in sorted(metrics)]
    rows += ["", "Original fictional fixtures; no medical authority or semantic-search claim.",
             "Citation accuracy is measured per response (all returned citations must be exact).",
             "Empty responses pass citation/isolation checks vacuously; no-answer is scored separately.",
             "", "## Per-case results", "", "| Case | First relevant rank | Extra checks |",
             "|---|---:|---|"]
    rows += [f"| {r['id']} | {r['rank']} | {r['extra_expectations']} |" for r in results]
    (output / "report.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("artifacts/ai-evals/knowledge-retrieval-v1"))
    args = parser.parse_args()
    report = run_evaluations(args.output)
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
