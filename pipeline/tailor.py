"""Report per-posting resume vocabulary coverage without an LLM.

    python -m pipeline.tailor --resume <resume.txt|.md> --jobs <jobs.jsonl> --out <tailor.jsonl>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
from typing import Iterable


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_KEYWORD_MAP = os.path.join(ROOT, "data", "keyword-map.json")
DEFAULT_TITLE_FAMILIES = os.path.join(ROOT, "data", "title-families.json")

ACRONYM = re.compile(r"^[A-Z0-9][A-Z0-9\.\+\#/\-]*$")
BULLETS = re.compile(r"[•●▪∙⁃◦·]")

REQUIREMENTS_HEADING = re.compile(
    r"(?:"
    r"(?:minimum|basic|required|preferred|desired|core|key|essential|technical|additional|other)\s+"
    r"(?:qualifications?|requirements?|skills?|experience|competenc\w+)"
    r"|qualifications?"
    r"|requirements?"
    r"|skills?\s*(?:&|and)\s*(?:experience|qualifications?)"
    r"|what\s+(?:you(?:'|’)?ll\s+need|you(?:(?:'|’)?ll|\s+will)?\s+bring|"
    r"we(?:'|’)?re\s+looking\s+for|we\s+(?:want|need|expect)|"
    r"makes?\s+you\s+a\s+(?:great|good|strong)\s+fit)"
    r"|required|preferred|skills|experience|education(?:al\s+background)?|must\s+have"
    r"|we(?:'|’)?re\s+looking\s+for\s+someone|you(?:'|’)?ll\s+be\s+a\s+(?:great|good)\s+fit"
    r"|who\s+you\s+are|about\s+you|you\s+(?:have|are|will\s+have|should\s+have)"
    r"|must[\s\-]haves?|nice[\s\-]to[\s\-]haves?|bonus(?:\s+points?)?|(?:it(?:'|’)?s\s+)?a\s+plus"
    r"|(?:the\s+)?ideal\s+candidate|your\s+(?:background|experience|profile)"
    r"|experience\s+(?:&|and)\s+skills?|skills?\s+(?:&|and)\s+abilities"
    r"|we(?:'|’)?d\s+love\s+to\s+(?:see|hear)|you\s+might\s+be\s+a\s+(?:great|good)\s+fit"
    r"|requirements?\s*(?:&|and)\s*qualifications?"
    r")",
    re.I,
)
NICE_HEADING = re.compile(
    r"nice[\s\-]to[\s\-]have|preferred|bonus|a\s+plus|desired|"
    r"we(?:'|’)?d\s+love|pluses|additional|good\s+to\s+have",
    re.I,
)
STOP_HEADING = re.compile(
    r"(?:responsibilit\w+|what\s+you(?:'|’)?ll\s+do|the\s+role|"
    r"about\s+(?:us|the\s+(?:company|team|role))|who\s+we\s+are|"
    r"our\s+(?:mission|team|story|values)|benefits?|perks?|compensation|salary|pay\s+range|"
    r"what\s+we\s+offer|why\s+(?:join|work)|equal\s+(?:employment\s+)?opportunit\w+|"
    r"eeo\b|diversity|accommodations?|how\s+to\s+apply|application\s+process|"
    r"interview\s+process|next\s+steps|day\s+(?:in\s+the\s+life|to\s+day)|"
    r"job\s+summary|position\s+summary|overview|the\s+opportunity|location|travel|"
    r"physical\s+(?:requirements|demands))",
    re.I,
)

DEFAULT_ABSENT = {
    "AWS", "Azure", "GCP", "Kubernetes", "Terraform", "LangChain", "LangGraph", "LlamaIndex",
    "CrewAI", "AutoGen", "Semantic Kernel", "PyTorch", "Snowflake", "Databricks", "BigQuery",
    "Airflow", "dbt", "Spark", "Kafka", "MongoDB", "Redis", "MySQL", "Pinecone", "Weaviate",
    "Chroma", "FAISS", "Bedrock", "Azure OpenAI", "Vertex AI", "fine-tuning", "deep learning",
    "MLOps", "LLMOps", "Java", "Go", "Rust", "C#", "Ruby", "Vue", "Angular", "Django",
    "Flask", "gRPC", "microservices", "Hugging Face", "Gemini", "Llama", "knowledge graph",
    "scikit-learn", "Copilot", "Retool", "Airtable", "Make.com", "Zapier", "n8n", "serverless",
    "computer vision", "SaaS", "enterprise", "roadmap", "mentorship", "agile", "bachelor's degree",
    "computer science",
}


def wb(s: str) -> str:
    esc = re.escape(s)
    esc = esc.replace(r"\ ", r"[\s\-]+")
    left = r"(?<![A-Za-z0-9])" if s[0].isalnum() else r""
    right = r"(?![A-Za-z0-9])" if s[-1].isalnum() else r""
    return left + esc + right


def matcher(form: str) -> re.Pattern[str]:
    flags = 0 if (ACRONYM.match(form) and len(form) >= 2) else re.I
    return re.compile(wb(form), flags)


def load_resume(path: str) -> str:
    raw = open(path, encoding="utf-8").read()
    fenced = re.search(r"## The résumé text\s*```(.+?)```", raw, re.DOTALL)
    return fenced.group(1) if fenced else raw


def load_rows(path: str) -> list[dict]:
    raw = open(path, encoding="utf-8").read()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # JSON Lines records end at LF. str.splitlines() also splits valid JSON
        # string content such as U+2028, which occurs in the real posting corpus.
        return [json.loads(line) for line in raw.split("\n") if line.strip()]
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [parsed]
    raise ValueError(f"jobs file must contain a JSON array or JSON objects: {path}")


def _heading_text(line: str) -> str:
    return re.sub(r":\s*$", "", line).strip()


def extract_requirement_blocks(description: str) -> tuple[str, str]:
    normalized = description.replace("\r\n", "\n").replace("\r", "\n")
    normalized = BULLETS.sub("\n• ", normalized)
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    found_heading = False
    active: str | None = None
    captured_in_block = 0
    must_lines: list[str] = []
    nice_lines: list[str] = []

    for line in lines:
        heading = _heading_text(line)
        is_short = len(heading) <= 90
        if is_short and REQUIREMENTS_HEADING.fullmatch(heading):
            found_heading = True
            active = "NICE" if NICE_HEADING.search(heading) else "MUST"
            captured_in_block = 0
            continue
        if is_short and STOP_HEADING.fullmatch(heading):
            active = None
            continue
        if active and captured_in_block < 45:
            (nice_lines if active == "NICE" else must_lines).append(line)
            captured_in_block += 1

    if not found_heading:
        return description, ""
    return "\n".join(must_lines), "\n".join(nice_lines)


def compile_vocab(vocab: dict[str, list[str]]) -> dict[str, list[tuple[str, re.Pattern[str]]]]:
    return {term: [(form, matcher(form)) for form in forms] for term, forms in vocab.items()}


def _first_surface(patterns: Iterable[tuple[str, re.Pattern[str]]], text: str) -> str | None:
    return next((form for form, pattern in patterns if pattern.search(text)), None)


def missing_status(term: str, terms: dict[str, dict]) -> str:
    verdict = (terms.get(term) or {}).get("verdict")
    if verdict in {"HAVE", "ADJACENT", "ABSENT"}:
        return verdict
    return "ABSENT" if term in DEFAULT_ABSENT else "ADJACENT"


def headline_hint(title: str, title_families: list[dict]) -> str:
    for family in title_families:
        if re.search(family["regex"], title, re.I):
            return family["name"]
    return "Other"


def tailor_posting(
    row: dict,
    resume: str,
    compiled_vocab: dict[str, list[tuple[str, re.Pattern[str]]]],
    terms: dict[str, dict],
    title_families: list[dict],
) -> dict:
    description = row["description"]
    must_text, nice_text = extract_requirement_blocks(description)
    asked: list[tuple[str, str, int]] = []
    for term, patterns in compiled_vocab.items():
        surface = _first_surface(patterns, must_text)
        if surface is None:
            surface = _first_surface(patterns, nice_text)
        if surface is None:
            continue
        count = sum(len(pattern.findall(description)) for _, pattern in patterns)
        asked.append((term, surface, count))

    matched_with_counts: list[tuple[str, int]] = []
    missing: list[dict] = []
    for term, surface, count in asked:
        if _first_surface(compiled_vocab[term], resume) is not None:
            matched_with_counts.append((term, count))
        else:
            missing.append({
                "term": term,
                "count_in_posting": count,
                "surface": surface,
                "status": missing_status(term, terms),
            })

    matched_with_counts.sort(key=lambda item: item[1], reverse=True)
    missing.sort(key=lambda item: item["count_in_posting"], reverse=True)
    total = len(matched_with_counts) + len(missing)
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "company": row.get("company"),
        "url": row.get("url"),
        "score": row.get("score"),
        "coverage_pct": round(100 * len(matched_with_counts) / total, 1) if total else 0.0,
        "matched": [term for term, _ in matched_with_counts],
        "missing": missing,
        "headline_hint": headline_hint(row.get("title") or "", title_families),
    }


def _score_value(row: dict) -> float:
    score = row.get("score")
    return float(score) if isinstance(score, (int, float)) else float("-inf")


def run_tailoring(
    resume_path: str,
    jobs_path: str,
    keyword_map_path: str,
    out_path: str,
    min_score: int = 50,
    top: int | None = None,
    scored_path: str | None = None,
    title_families_path: str = DEFAULT_TITLE_FAMILIES,
) -> dict:
    resume = load_resume(resume_path)
    keyword_map = json.load(open(keyword_map_path, encoding="utf-8"))
    families = json.load(open(title_families_path, encoding="utf-8"))["title_families"]
    compiled = compile_vocab(keyword_map["vocab"])

    rows = load_rows(jobs_path)
    rows = [r for r in rows if "score" not in r or _score_value(r) >= min_score]
    rows.sort(key=_score_value, reverse=True)
    if top is not None:
        rows = rows[:top]

    if scored_path is None:
        candidate = os.path.join(os.path.dirname(os.path.abspath(jobs_path)), "scored.jsonl")
        scored_path = candidate if os.path.exists(candidate) else None
    descriptions: dict[str, str] = {}
    if scored_path:
        descriptions = {
            str(r.get("id")): r["description"]
            for r in load_rows(scored_path)
            if r.get("id") is not None and isinstance(r.get("description"), str) and r["description"].strip()
        }

    output: list[dict] = []
    skipped = 0
    total = len(rows)
    for source_row in rows:
        row = dict(source_row)
        description = row.get("description")
        if not isinstance(description, str) or not description.strip():
            description = descriptions.get(str(row.get("id")))
        if not description:
            skipped += 1
            continue
        row["description"] = description
        output.append(tailor_posting(row, resume, compiled, keyword_map.get("terms", {}), families))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for tailored in output:
            fh.write(json.dumps(tailored, ensure_ascii=False) + "\n")

    med = round(statistics.median(r["coverage_pct"] for r in output), 1) if output else 0.0
    return {"rows": len(output), "median_coverage_pct": med, "skipped_no_description": skipped, "input_rows": total}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", required=True)
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--keyword-map", default=DEFAULT_KEYWORD_MAP)
    ap.add_argument("--out", required=True)
    ap.add_argument("--top", type=int)
    ap.add_argument("--min-score", type=int, default=50)
    ap.add_argument("--scored")
    ap.add_argument("--title-families", default=DEFAULT_TITLE_FAMILIES)
    a = ap.parse_args()
    summary = run_tailoring(
        a.resume, a.jobs, a.keyword_map, a.out, a.min_score, a.top, a.scored, a.title_families
    )
    if summary["skipped_no_description"]:
        print(f"skipped {summary['skipped_no_description']} of {summary['input_rows']} rows: no description available")
    print(json.dumps({k: summary[k] for k in ("rows", "median_coverage_pct", "skipped_no_description")}))


if __name__ == "__main__":
    main()
