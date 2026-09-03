"""Shared helpers for job-radar source scrapers. Keyless, headless, no browser."""
from __future__ import annotations
import argparse, hashlib, json, os, re, sys
from datetime import datetime, timezone
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = "job-radar/0.1 (personal job search; contact endlesslime9@gmail.com)"
CUTOFF = "2026-07-19"


def session(extra_headers: dict | None = None) -> requests.Session:
    s = requests.Session()
    r = Retry(total=3, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504],
              allowed_methods=["GET", "POST"])
    s.mount("https://", HTTPAdapter(max_retries=r))
    s.mount("http://", HTTPAdapter(max_retries=r))
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/html;q=0.9, */*;q=0.8"})
    if extra_headers:
        s.headers.update(extra_headers)
    return s


TITLE_HITS = [
    "ai engineer", "ai developer", "ai automation", "automation engineer", "agentic", "ai agent",
    "agent engineer", "llm", "applied ai", "forward deployed", "forward-deployed", "ai solutions",
    "genai", "gen ai", "generative ai", "ai product", "ai operations", "ai ops", "aiops",
    "prompt engineer", "workflow automation", "ai integration", "machine learning engineer",
    "ml engineer", "ai specialist", "ai consultant", "claude", "n8n", "langchain", "rag ",
    "ai platform", "ai software", "ai full", "ai/ml", "ai & ", "ai and ", "artificial intelligence",
    "conversational ai", "voice ai", "ai workflow", "ai implementation", "ai architect",
]
DESC_AI = ["llm", "large language model", "agents", "agentic", "claude", "anthropic", "openai",
           "gpt", "langchain", "langgraph", "rag", "retrieval-augmented", "ai-native", "ai native",
           "copilot", "cursor", "vector", "embedding", "prompt", "n8n", "zapier", "make.com"]
GENERIC_TITLE = ["software engineer", "developer", "full-stack", "full stack", "fullstack",
                 "backend", "frontend", "engineer", "programmer", "technical"]
REJECT = ["sales", "recruiter", "recruiting", "marketing manager", "account executive",
          "data labeling", "data annotation", "ai trainer", "phd", "ph.d", "speech data",
          "voice recording", "native speaker", "transcriptionist", "business development",
          "customer success"]


def is_fit(title: str, description: str = "") -> bool:
    t = (title or "").lower()
    d = (description or "").lower()
    if any(r in t for r in REJECT):
        return False
    if any(h in t for h in TITLE_HITS):
        return True
    if any(g in t for g in GENERIC_TITLE) and any(k in d for k in DESC_AI):
        return True
    return False


def seniority(title: str) -> str:
    t = (title or "").lower()
    if any(w in t for w in ["staff", "principal", "director", "head of", "vp ", "chief", "distinguished"]):
        return "staff"
    if any(w in t for w in ["senior", "sr.", "sr ", "lead"]):
        return "senior"
    if any(w in t for w in ["junior", "jr.", "jr ", "entry", "associate", "early career", "graduate", "intern", "apprentice"]):
        return "entry"
    return "mid" if t else "unknown"


_US_STATES = ("al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny nc nd oh ok "
              "or pa ri sc sd tn tx ut vt va wa wv wi wy dc").split()
_US_STATE_NAMES = ["alabama", "alaska", "arizona", "arkansas", "colorado", "connecticut", "delaware", "florida", "georgia",
                   "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland",
                   "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
                   "new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio",
                   "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee",
                   "texas", "utah", "vermont", "virginia", "washington", "wisconsin", "wyoming", "seattle", "boston",
                   "austin", "denver", "chicago", "atlanta", "miami", "dallas", "houston", "phoenix", "portland", "nyc",
                   "philadelphia", "pittsburgh", "minneapolis", "nashville", "salt lake", "raleigh", "durham", "detroit"]
_CA = ["san francisco", "bay area", "napa", "oakland", "san jose", "palo alto", "mountain view", "menlo park",
       "sunnyvale", "berkeley", "redwood city", "san mateo", "santa clara", "los angeles", "san diego", "california",
       "santa rosa", "sacramento", "irvine", "cupertino", "fremont", "emeryville", "south san francisco"]
_EU = ["europe", "emea", "united kingdom", "london", "berlin", "germany", "netherlands", "amsterdam", "france", "paris",
       "spain", "madrid", "barcelona", "poland", "warsaw", "portugal", "lisbon", "ireland", "dublin", "sweden", "stockholm",
       "denmark", "copenhagen", "norway", "finland", "italy", "milan", "switzerland", "zurich", "austria", "vienna",
       "belgium", "brussels", "czech", "prague", "hungary", "budapest", "romania", "bucharest", "estonia", "tallinn",
       "greece", "athens", "england", "scotland", "munich", "hamburg", ", gb", ", uk", ", de", ", fr", ", nl", ", es",
       ", pl", ", pt", ", ie", ", se", ", dk", ", ch", ", at", ", be", ", cz", ", hu", ", ro", ", it"]


def region_of(location: str, remote: bool | None) -> str:
    l = " " + re.sub(r"[\(\)\[\]/|]+", " ", (location or "").lower()).strip() + " "
    if any(w in l for w in _CA) or re.search(r"[, ]ca[ ,]", l) or re.search(r"\bsf\b", l):
        return "us-ca"
    if any(w in l for w in ["worldwide", "anywhere", "global", "international"]):
        return "worldwide"
    if re.fullmatch(r"\s*(remote|fully remote|remote remote|100% remote)\s*", l):
        return "unknown"
    if (re.search(r"\b(us|usa|u\.s\.a?|united states|america|north america|americas)\b", l)
            or any(w in l for w in _US_STATE_NAMES)
            or re.search(r",\s?(" + "|".join(_US_STATES) + r")\b", l)):
        return "us"
    if any(w in l for w in _EU) or re.search(r"\buk\b", l):
        return "eu"
    if not l.strip():
        return "unknown"
    return "other"


def emp_type(text: str) -> str:
    t = (text or "").lower()
    if "intern" in t:
        return "internship"
    if "part-time" in t or "part time" in t:
        return "part-time"
    if any(w in t for w in ["contract", "freelance", "1099", "hourly", "consultant"]):
        return "contract"
    if any(w in t for w in ["full-time", "full time", "fulltime", "permanent"]):
        return "full-time"
    return "unknown"


def parse_salary(text: str) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    nums = re.findall(r"\$?\s?(\d{2,3}(?:,\d{3})+|\d{2,3}k|\d{5,6})", text.lower())
    vals = []
    for n in nums:
        n = n.replace(",", "")
        vals.append(int(n[:-1]) * 1000 if n.endswith("k") else int(n))
    vals = [v for v in vals if 20000 <= v <= 1000000]
    if not vals:
        return None, None
    return min(vals), max(vals)


def clean(text: str, limit: int = 8000) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>|</p>|</li>|</div>|</h\d>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&amp;", "&", text).replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'").replace("&quot;", '"')
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()[:limit]


def make_id(source: str, native: str | None, url: str) -> str:
    key = native or hashlib.sha1(url.encode()).hexdigest()[:12]
    return f"{source}:{key}"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def recent(posted_at: str | None) -> bool:
    return posted_at is None or posted_at >= CUTOFF


def record(source, title, company, url, *, apply_url=None, location="", remote=None,
           employment_type=None, salary=None, posted_at=None, description="", tags=None,
           open_status="unverified", notes=None, native_id=None):
    smin, smax = parse_salary(salary or "")
    desc = clean(description)
    return {
        "id": make_id(source, native_id, url), "source": source, "title": (title or "").strip(),
        "company": (company or "").strip(), "url": url, "apply_url": apply_url,
        "location": location or "", "remote": remote, "region": region_of(location, remote),
        "employment_type": employment_type or emp_type(f"{title} {location} {desc[:600]}"),
        "salary": salary, "salary_min": smin, "salary_max": smax, "posted_at": posted_at,
        "description": desc, "tags": tags or [], "seniority": seniority(title),
        "open_status": open_status, "fetched_at": now(), "notes": notes,
    }


def cli(description: str) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--out", required=True, help="output .jsonl path")
    p.add_argument("--limit", type=int, default=0)
    return p.parse_args()


def write_jsonl(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} rows -> {path}", file=sys.stderr)
