"""Fit score (0-100) for Jasper: self-taught AI automation / agentic engineer, Napa CA, remote or
Bay Area, contract or full-time, entry to mid. Explains every score in `why`.

    python pipeline/score.py --in <data>/verified.jsonl --out <data>/scored.jsonl
"""
from __future__ import annotations
import argparse, json, os, re, sys
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sources"))
import common  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
TODAY = date(2026, 9, 2)

TITLE_TIERS = [
    (32, ["agentic", "ai automation", "automation engineer", "forward deployed", "forward-deployed",
          "ai operations", "ai ops", "ai engineer", "ai developer", "agent engineer", "ai agent"]),
    (26, ["applied ai", "llm", "ai solutions", "prompt engineer", "ai integration", "ai product",
          "genai", "gen ai", "generative ai", "ai specialist", "ai implementation", "workflow automation",
          "ai workflow", "ai full", "ai software", "conversational ai", "voice ai", "ai consultant", "claude", "n8n"]),
    (18, ["ai platform", "ai/ml", "artificial intelligence", "ai architect", "ai & ", "ai and "]),
    (12, ["machine learning engineer", "ml engineer"]),
]
SELF_TAUGHT = ["self-taught", "self taught", "no degree", "bootcamp", "non-traditional", "nontraditional",
               "portfolio over", "links over resume", "links over résumé", "regardless of background",
               "open on background", "don't tick every box", "do not tick every box", "no experience required",
               "not a professional developer", "early career", "new grad", "entry level", "entry-level"]
AGENT_TOOLS = ["claude code", "cursor", "codex", "aider", "copilot", "agentic", "ai coding agent", "coding agents"]
NOCODE = ["n8n", "zapier", "make.com", "gumloop", "airtable", "retool"]
STACK = ["typescript", "next.js", "nextjs", "node", "python", "supabase", "postgres", "react native", "expo",
         "playwright", "scraping", "scraper", "vercel", "tailwind"]


TITLE_PENALTY = {  # role families that share words with the target but are not the target
    -30: ["manager", "marketer", "marketing", "data scientist", "research scientist", "scientist", "recruiter",
          "sales", "account executive", "designer", "writer", "analyst", "program manager", "product manager",
          "project manager", "director", "head of", "attorney", "counsel", "coach", "teacher", "instructor",
          "enrollment", "medical", "clinical", "nurse", "representative", "power system", "civil", "structural",
          "customer support", "support specialist", "virtual assistant", "executive assistant", "bookkeep",
          "closer", "commission", "product owner", "evaluator", "mentor", "annotat", "rater", "labeler"],
    -40: ["looking for a job", "looking for an", "seeking a job", "seeking job", "transitioning", "[for hire]",
          "open to work", "my resume", "am i qualified", "how do i get", "advice", "im looking for", "i am looking",
          "i'm looking", "looking for a remote job", "looking for referal", "looking for referral",
          "looking for a remote opportunity", "fresh graduate", "looking for work", "looking for opportunit",
          "werkstudent", "working student", "co-op", "student looking", "student seeking", "looking for … co-op",
          "looking for leads", "looking for co-op"],
    -12: ["architect"],
    -28: ["campus", "undergraduate", "class of 20", "university program", "phd", "postdoc", "fellow"],
    -22: ["qa ", "qa/", "quality", "test engineer", "sdet", "automation tester", "uipath", "blue prism",
          "automation anywhere", "rpa", "security engineer", "network", "sap ", "servicenow", "salesforce",
          "workday", "sre", "site reliability", "devops", "cloud infra", "infrastructure", "accounting",
          "hardware", "embedded", "firmware", "mechanical", "electrical", "controls engineer", "plc",
          "data engineer", "data scientist", "mlops", "pytorch", "computer vision", "robotics", "mobile engineer"],
}
AI_WORDS = ["ai", "llm", "agent", "agentic", "claude", "gpt", "openai", "anthropic", "langchain", "rag",
            "prompt", "copilot", "n8n", "zapier", "make.com", "automation"]


def years_required(d: str):
    m = re.findall(r"(\d{1,2})\s*\+?\s*(?:years|yrs)", d)
    vals = [int(x) for x in m if int(x) <= 20]
    return max(vals) if vals else None


def score(r: dict) -> tuple[int, list[str]]:
    t = (r.get("title") or "").lower()
    d = (r.get("description") or "").lower()
    why = []
    s = 0
    for pts, words in TITLE_TIERS:
        if any(w in t for w in words):
            # "automation engineer" with no AI word anywhere in the title is usually QA/RPA/infra work
            if "automation engineer" in t and not any(w in t for w in ["ai", "agent", "llm", "workflow", "intelligent"]):
                ai_in_body = sum(d.count(w) for w in ["llm", "agent", "claude", "gpt", "openai", "langchain", "n8n", "zapier"])
                pts = 20 if ai_in_body >= 2 else 8
            s += pts
            why.append(f"title +{pts}")
            break
    else:
        dev_title = any(g in t for g in common.GENERIC_TITLE)
        ai_body = sum(1 for w in common.DESC_AI if w in d)
        if dev_title and ai_body >= 2:
            s += 10; why.append(f"generic dev title, AI body x{ai_body} +10")
        elif dev_title:
            s -= 10; why.append("generic dev title, weak AI body -10")
        else:
            s -= 35; why.append("not a dev/AI title -35")
    if r.get("source") == "reddit" and not re.search(r"hiring|looking for|seeking|need a|wanted|we're looking|we are looking|\bjob\b.*(open|available)", t):
        s -= 45; why.append("reddit post without a hiring marker -45")
    for pts, words in TITLE_PENALTY.items():
        hit = next((w for w in words if w in t), None)
        if hit:
            s += pts
            why.append(f"'{hit.strip()}' role {pts}")
            break
    sen = r.get("seniority")
    pts = {"entry": 22, "mid": 16, "unknown": 12, "senior": 3, "staff": -12}[sen if sen in ("entry", "mid", "senior", "staff") else "unknown"]
    s += pts
    why.append(f"{sen} {'+' if pts >= 0 else ''}{pts}")
    region, remote = r.get("region"), r.get("remote")
    loc = (r.get("location") or "").lower()
    if remote and region in ("worldwide", "us", "unknown"):
        s += 20; why.append("remote +20")
    elif region == "us-ca":
        s += 15 if not remote else 20; why.append("Bay Area/CA +15" if not remote else "remote CA +20")
    elif remote and region == "eu":
        s -= 6; why.append("remote but EU-scoped -6")
    elif remote is None and not loc:
        s += 8; why.append("location unknown +8")
    elif remote is None and region in ("us", "worldwide", "unknown"):
        s += 4; why.append("US location, remote not stated +4")
    elif remote is None:
        s -= 10; why.append("non-US location, remote not stated -10")
    else:
        s -= 22; why.append("onsite elsewhere -22")
    et = r.get("employment_type")
    # internship -30: an "intern" title also reads as entry (+22), so -6 left Summer-2027 internships at 100 (seen 2026-09-03)
    s += {"contract": 8, "full-time": 6, "part-time": 5, "internship": -30}.get(et, 3)
    why.append(f"{et} {'+' if et != 'internship' else ''}{ {'contract': 8, 'full-time': 6, 'part-time': 5, 'internship': -30}.get(et, 3)}")
    if r.get("aggregator"):
        s -= 6; why.append("aggregator repost -6")
    if any(w in d for w in SELF_TAUGHT):
        s += 10; why.append("self-taught friendly +10")
    if any(w in d for w in AGENT_TOOLS):
        s += 8; why.append("names agent tools +8")
    if any(w in d for w in NOCODE):
        s += 4; why.append("automation tools +4")
    stack_hits = sum(1 for w in STACK if w in d)
    if stack_hits >= 3:
        s += 6; why.append(f"stack overlap ({stack_hits}) +6")
    yrs = years_required(d)
    if yrs is not None:
        if yrs >= 8:
            s -= 14; why.append(f"{yrs}+ yrs -14")
        elif yrs >= 5:
            s -= 7; why.append(f"{yrs}+ yrs -7")
        elif yrs <= 2:
            s += 4; why.append(f"{yrs} yrs +4")
    if "phd" in d and ("required" in d or "must" in d):
        s -= 12; why.append("PhD wording -12")
    offshore = re.search(r"(latam|latin america|eastern europe|pakistan|india|philippines|south africa|nigeria|vietnam|bangladesh)[^.\n]{0,60}(preferred|only|based|required|must)", d) \
        or re.search(r"(based|located|residing)\s+in\s+(latam|latin america|eastern europe|pakistan|india|the philippines|south africa|nigeria|vietnam)", d)
    if offshore and not re.search(r"\b(us|usa|united states|u\.s\.)\b[^.\n]{0,40}(based|located|only|residents?|authorized)", d):
        s -= 18; why.append("offshore-preferred wording -18")
    if "security clearance" in d or "clearance required" in d:
        s -= 20; why.append("clearance -20")
    if any(w in d for w in ["must be based in", "must reside in", "only candidates in"]) and "california" not in d and "united states" not in d and " us " not in d:
        s -= 8; why.append("residency restriction -8")
    p = r.get("posted_at")
    if p:
        try:
            age = (TODAY - date.fromisoformat(p[:10])).days
            if age <= 7:
                s += 10; why.append(f"{age}d old +10")
            elif age <= 14:
                s += 6; why.append(f"{age}d old +6")
            elif age <= 30:
                s += 2; why.append(f"{age}d old +2")
            else:
                s -= 5; why.append(f"{age}d old -5")
        except ValueError:
            pass
    v = (r.get("verify") or {}).get("status")
    if v == "open":
        s += 5; why.append("verified open +5")
    elif v == "closed":
        s -= 100; why.append("CLOSED")
    return max(0, min(100, s)), why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.inp, encoding="utf-8") if l.strip()]
    for r in rows:
        r["score"], r["why"] = score(r)
    rows.sort(key=lambda r: (-r["score"], r.get("posted_at") or ""))
    with open(a.out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    buckets = {"80+": 0, "65-79": 0, "50-64": 0, "<50": 0}
    for r in rows:
        k = "80+" if r["score"] >= 80 else "65-79" if r["score"] >= 65 else "50-64" if r["score"] >= 50 else "<50"
        buckets[k] += 1
    print(json.dumps({"rows": len(rows), "buckets": buckets}, indent=1))


if __name__ == "__main__":
    main()
