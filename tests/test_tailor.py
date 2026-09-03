import csv
import json
import sys
from unittest.mock import patch

import run as run_module
from pipeline import export
from pipeline.tailor import (
    compile_vocab,
    extract_requirement_blocks,
    load_resume,
    load_rows,
    run_tailoring,
    tailor_posting,
)


FAMILIES = [{"name": "AI Engineer", "regex": r"AI\s+Engineer"}]


def result(description, resume, vocab, terms=None):
    row = {
        "id": "job:1",
        "title": "AI Engineer",
        "company": "Example",
        "url": "https://example.test/job",
        "score": 90,
        "description": description,
    }
    return tailor_posting(row, resume, compile_vocab(vocab), terms or {}, FAMILIES)


def test_acronym_case_sensitive_but_regular_term_case_insensitive():
    description = "Requirements:\n• REST APIs\n• Python"
    vocab = {"REST": ["REST"], "Python": ["Python"]}

    covered = result(description, "Built REST services with python.", vocab)
    lowercase_only = result(description, "Please get some rest. Uses PYTHON.", vocab)

    assert covered["matched"] == ["REST", "Python"]
    assert lowercase_only["matched"] == ["Python"]
    assert [item["term"] for item in lowercase_only["missing"]] == ["REST"]


def test_word_boundaries_prevent_substring_match():
    tailored = result("Must have:\nRAG experience", "Designed stoRAGe and storage systems.", {"RAG": ["RAG"]})

    assert tailored["matched"] == []
    assert [item["term"] for item in tailored["missing"]] == ["RAG"]


def test_missing_status_uses_verdict_and_named_vendor_default():
    vocab = {"RAG": ["RAG"], "Communication": ["communication"], "AWS": ["AWS"]}
    terms = {
        "RAG": {"verdict": "HAVE"},
        "Communication": {"verdict": None},
        "AWS": {"verdict": None},
    }
    tailored = result("Requirements:\nRAG\nCommunication\nAWS", "unrelated", vocab, terms)

    assert {item["term"]: item["status"] for item in tailored["missing"]} == {
        "RAG": "HAVE",
        "Communication": "ADJACENT",
        "AWS": "ABSENT",
    }


def test_coverage_math_for_known_subset():
    vocab = {"Python": ["Python"], "RAG": ["RAG"], "Claude": ["Claude"]}
    tailored = result("Required skills:\nPython\nRAG\nClaude", "PYTHON and Claude", vocab)

    assert tailored["coverage_pct"] == 66.7
    assert tailored["matched"] == ["Python", "Claude"]


def test_jsonl_loader_does_not_split_unicode_line_separator(tmp_path):
    jobs = tmp_path / "jobs.jsonl"
    rows = [{"id": "one", "description": "before\u2028after"}, {"id": "two", "description": "plain"}]
    jobs.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

    assert load_rows(str(jobs)) == rows


def test_resume_loader_uses_only_the_named_fenced_block(tmp_path):
    resume = tmp_path / "resume.md"
    resume.write_text("Ignore AWS here.\n## The résumé text\n```Python only\n```\nIgnore RAG here.", encoding="utf-8")

    assert load_resume(str(resume)).strip() == "Python only"


def test_requirement_blocks_label_nice_sections_and_stop_at_role_heading():
    description = (
        "Required qualifications:\nPython\n"
        "Nice-to-haves:\nAWS\n"
        "Responsibilities:\nRAG implementation"
    )

    must, nice = extract_requirement_blocks(description)

    assert must == "Python"
    assert nice == "AWS"


def test_run_tailoring_joins_descriptions_filters_scores_and_applies_top(tmp_path):
    resume = tmp_path / "resume.txt"
    resume.write_text("Python", encoding="utf-8")
    keyword_map = tmp_path / "keywords.json"
    keyword_map.write_text(json.dumps({"vocab": {"Python": ["Python"]}, "terms": {}}), encoding="utf-8")
    families = tmp_path / "families.json"
    families.write_text(json.dumps({"title_families": []}), encoding="utf-8")
    jobs = tmp_path / "target-list.jsonl"
    jobs.write_text("\n".join([
        json.dumps({"id": "high", "title": "High", "score": 90}),
        json.dumps({"id": "middle", "title": "Middle", "score": 80}),
        json.dumps({"id": "low", "title": "Low", "score": 40}),
    ]) + "\n", encoding="utf-8")
    scored = tmp_path / "full.jsonl"
    scored.write_text("\n".join([
        json.dumps({"id": "high", "description": "Requirements:\nPython"}),
        json.dumps({"id": "middle", "description": "Requirements:\nPython"}),
        json.dumps({"id": "low", "description": "Requirements:\nPython"}),
    ]) + "\n", encoding="utf-8")
    out = tmp_path / "tailor.jsonl"

    summary = run_tailoring(
        str(resume), str(jobs), str(keyword_map), str(out), min_score=50, top=1,
        scored_path=str(scored), title_families_path=str(families),
    )

    output = load_rows(str(out))
    assert summary == {"rows": 1, "median_coverage_pct": 100.0, "skipped_no_description": 0, "input_rows": 1}
    assert [row["id"] for row in output] == ["high"]


def test_pipeline_aborts_before_export_when_tailoring_fails():
    argv = ["run.py", "--skip-sources", "--resume", "resume.txt"]
    with patch.object(sys, "argv", argv), patch.object(run_module.os, "makedirs"), \
            patch.object(run_module, "run", side_effect=[0, 0, 0, 7]) as invoke:
        try:
            run_module.main()
        except SystemExit as exc:
            assert exc.code == 7
        else:
            raise AssertionError("tailoring failure did not stop the pipeline")

    assert invoke.call_count == 4
    assert all(call.args[1] != "export" for call in invoke.call_args_list)


def scored_row():
    return {
        "id": "job:1",
        "score": 90,
        "title": "AI Engineer",
        "company": "Example",
        "seniority": "mid",
        "employment_type": "full-time",
        "remote": True,
        "region": "us",
        "location": "Remote",
        "salary": None,
        "posted_at": "2026-09-01",
        "verify": {"status": "open"},
        "sources": ["ats"],
        "url": "https://example.test/job",
        "apply_url": None,
        "why": ["title +32"],
        "description": "Requirements: Python",
        "tags": [],
    }


def test_export_adds_tailor_columns_when_tailor_file_exists(tmp_path):
    inp = tmp_path / "scored.jsonl"
    inp.write_text(json.dumps(scored_row()) + "\n", encoding="utf-8")
    tailor = {
        "id": "job:1",
        "coverage_pct": 50.0,
        "missing": [
            {"term": "RAG", "count_in_posting": 3},
            {"term": "AWS", "count_in_posting": 1},
        ],
    }
    (tmp_path / "tailor.jsonl").write_text(json.dumps(tailor) + "\n", encoding="utf-8")
    outdir = tmp_path / "out"

    export.export(str(inp), str(outdir))

    with open(outdir / "jobs.csv", encoding="utf-8", newline="") as fh:
        csv_row = next(csv.DictReader(fh))
    json_row = json.loads((outdir / "jobs.json").read_text(encoding="utf-8"))[0]
    assert csv_row["coverage_pct"] == "50.0"
    assert csv_row["top_missing"] == "RAG;AWS"
    assert json_row["coverage_pct"] == 50.0
    assert json_row["top_missing"] == "RAG;AWS"


def test_export_without_tailor_preserves_previous_schema(tmp_path):
    inp = tmp_path / "scored.jsonl"
    inp.write_text(json.dumps(scored_row()) + "\n", encoding="utf-8")
    outdir = tmp_path / "out"

    export.export(str(inp), str(outdir))

    with open(outdir / "jobs.csv", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        csv_row = next(reader)
        assert reader.fieldnames == export.BASE_COLS
    json_row = json.loads((outdir / "jobs.json").read_text(encoding="utf-8"))[0]
    assert "coverage_pct" not in csv_row
    assert "top_missing" not in csv_row
    assert "coverage_pct" not in json_row
    assert "top_missing" not in json_row
