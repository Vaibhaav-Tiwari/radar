"""
exa_service.py — Exa AI integration for competitor discovery and intelligence.

Discovery strategy (correct approach):
  1. Neural-search for "{company} competitors alternatives" → finds editorial
     articles that name real rival products (e.g. "Best Notion Alternatives").
  2. Extract candidate company names + homepage URLs from those articles via
     a second Exa findSimilar call anchored to each article URL — OR we parse
     the article text to pull out product names, then do a keyword search to
     resolve their homepage URL.
  3. For each confirmed competitor homepage, enrich with news / jobs / product
     updates via parallel Exa searches.
"""

import asyncio
import os
import re
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

load_dotenv()

EXA_API_KEY = os.getenv("EXA_API_KEY", "")
EXA_BASE_URL = "https://api.exa.ai"

HEADERS = {
    "x-api-key": EXA_API_KEY,
    "Content-Type": "application/json",
    "accept": "application/json",
}

# ── Step 1: Discover competitor names from editorial articles ─────────────────

async def find_competitor_names(company_name: str, company_url: str, num_names: int = 8) -> list[str]:
    """
    Search for articles listing competitors / alternatives to the target company.
    Extract product/company names mentioned in those articles.
    Returns a deduplicated list of competitor names (strings).
    """
    domain = _extract_domain(company_url)

    queries = [
        f"best alternatives to {company_name} {_infer_category(company_name)}",
        f"{company_name} competitors comparison",
        f"top {_infer_category(company_name)} tools like {company_name}",
    ]

    candidate_names: list[str] = []

    async with httpx.AsyncClient(timeout=30) as client:
        for query in queries:
            try:
                resp = await client.post(
                    f"{EXA_BASE_URL}/search",
                    headers=HEADERS,
                    json={
                        "query": query,
                        "numResults": 3,
                        "useAutoprompt": True,
                        "type": "neural",
                        "excludeDomains": [domain],
                        "contents": {
                            "text": {"maxCharacters": 2000},
                        },
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                for r in data.get("results", []):
                    names = _parse_product_names_from_text(
                        r.get("text", "") + " " + r.get("title", ""),
                        exclude_domain=domain,
                        exclude_name=company_name,
                    )
                    candidate_names.extend(names)
            except Exception:
                continue

    # Deduplicate preserving order
    seen = set()
    unique: list[str] = []
    for n in candidate_names:
        key = n.lower()
        if key not in seen:
            seen.add(key)
            unique.append(n)

    return unique[:num_names]


# ── Step 2: Resolve competitor name → homepage URL ────────────────────────────

async def resolve_homepage(company_name: str) -> dict | None:
    """
    Given a company name, find their homepage URL and a short description
    using Exa keyword search targeting homepage-like pages.
    """
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.post(
                f"{EXA_BASE_URL}/search",
                headers=HEADERS,
                json={
                    "query": f"{company_name} official website homepage",
                    "numResults": 3,
                    "useAutoprompt": False,
                    "type": "keyword",
                    "contents": {
                        "text": {"maxCharacters": 400},
                        "highlights": {"numSentences": 2, "highlightsPerUrl": 1},
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])

            # Pick the result whose domain best matches the company name
            for r in results:
                url = r.get("url", "")
                d = _extract_domain(url)
                slug = d.split(".")[0].lower()
                name_slug = re.sub(r"[^a-z0-9]", "", company_name.lower())
                if name_slug in slug or slug in name_slug:
                    return {
                        "name": company_name,
                        "url": _ensure_homepage(url),
                        "description": _extract_description(r),
                    }

            # Fallback: just take the first result
            if results:
                r = results[0]
                return {
                    "name": company_name,
                    "url": _ensure_homepage(r.get("url", "")),
                    "description": _extract_description(r),
                }
        except Exception:
            return None

    return None


# ── Step 3: Find similar companies via Exa findSimilar (used as a supplement) ─

async def find_similar_via_exa(url: str, num_results: int = 4) -> list[dict]:
    """
    Exa findSimilar anchored to the target URL — returns semantically similar
    homepages. Used as a supplement when article parsing yields few results.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{EXA_BASE_URL}/findSimilar",
                headers=HEADERS,
                json={
                    "url": url,
                    "numResults": num_results,
                    "excludeSourceDomain": True,
                    # Filter to homepage-like results only
                    "category": "company",
                    "contents": {
                        "text": {"maxCharacters": 500},
                        "highlights": {"numSentences": 2, "highlightsPerUrl": 1},
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()
            companies = []
            for r in data.get("results", []):
                companies.append({
                    "name": _extract_company_name(r),
                    "url": _ensure_homepage(r.get("url", "")),
                    "description": _extract_description(r),
                })
            return companies
        except Exception:
            return []


# ── Master discovery function ─────────────────────────────────────────────────

async def discover_competitors(target_url: str, num_results: int = 6) -> list[dict]:
    """
    Full two-stage competitor discovery:
      Stage A — article mining (high precision: real named rivals)
      Stage B — findSimilar supplement (fills gaps)
    Merges and deduplicates, returns up to num_results companies.
    """
    company_name = _guess_company_name(target_url)

    # Stage A: article mining
    names = await find_competitor_names(company_name, target_url, num_names=10)

    # Resolve names to homepage objects in parallel
    resolved = await asyncio.gather(*[resolve_homepage(n) for n in names])
    stage_a = [c for c in resolved if c and c.get("url")]

    # Stage B: findSimilar supplement
    stage_b = await find_similar_via_exa(target_url, num_results=4)

    # Merge, deduplicate by domain
    seen_domains: set[str] = set()
    merged: list[dict] = []

    target_domain = _extract_domain(target_url)

    for company in stage_a + stage_b:
        if not company or not company.get("url"):
            continue
        d = _extract_domain(company["url"])
        if d == target_domain or d in seen_domains:
            continue
        seen_domains.add(d)
        merged.append(company)
        if len(merged) >= num_results:
            break

    return merged


# ── Intelligence enrichment ───────────────────────────────────────────────────

async def get_recent_news(company_name: str, company_url: str) -> list[dict]:
    """Recent news, funding rounds, and press coverage."""
    domain = _extract_domain(company_url)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{EXA_BASE_URL}/search",
            headers=HEADERS,
            json={
                "query": f"{company_name} funding raise announcement news 2024 2025",
                "numResults": 3,
                "useAutoprompt": True,
                "type": "neural",
                "excludeDomains": [domain],
                "contents": {
                    "text": {"maxCharacters": 300},
                    "highlights": {"numSentences": 2, "highlightsPerUrl": 1},
                },
            },
        )
        resp.raise_for_status()
        data = resp.json()
        articles = []
        for r in data.get("results", []):
            articles.append({
                "title": r.get("title", "Untitled"),
                "url": r.get("url", ""),
                "snippet": _extract_snippet(r),
                "published_date": r.get("publishedDate", ""),
                "source": _extract_domain(r.get("url", "")),
            })
        return articles


async def get_job_signals(company_name: str) -> list[dict]:
    """Hiring signals from job boards — reveals growth direction."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{EXA_BASE_URL}/search",
            headers=HEADERS,
            json={
                "query": f"{company_name} hiring engineer product designer",
                "numResults": 3,
                "useAutoprompt": False,
                "type": "keyword",
                "includeDomains": [
                    "linkedin.com", "greenhouse.io", "lever.co",
                    "jobs.ashbyhq.com", "wellfound.com", "careers.notion.so"
                ],
                "contents": {"text": {"maxCharacters": 200}},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        jobs = []
        for r in data.get("results", []):
            jobs.append({
                "title": r.get("title", "Open Role"),
                "url": r.get("url", ""),
                "source": _extract_domain(r.get("url", "")),
                "published_date": r.get("publishedDate", ""),
            })
        return jobs


async def get_product_updates(company_name: str, company_url: str) -> list[dict]:
    """Recent product launches, changelog entries, feature announcements."""
    domain = _extract_domain(company_url)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{EXA_BASE_URL}/search",
            headers=HEADERS,
            json={
                "query": f"{company_name} new feature launch update release changelog 2024 2025",
                "numResults": 3,
                "useAutoprompt": True,
                "type": "neural",
                "contents": {
                    "text": {"maxCharacters": 300},
                    "highlights": {"numSentences": 2, "highlightsPerUrl": 1},
                },
            },
        )
        resp.raise_for_status()
        data = resp.json()
        updates = []
        for r in data.get("results", []):
            updates.append({
                "title": r.get("title", "Product Update"),
                "url": r.get("url", ""),
                "snippet": _extract_snippet(r),
                "source": _extract_domain(r.get("url", "")),
                "published_date": r.get("publishedDate", ""),
            })
        return updates


# ── Text parsing: extract product names from article text ────────────────────

# Common stop-words to filter out from name candidates
_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "your", "their",
    "have", "more", "also", "some", "other", "best", "top", "free", "new",
    "like", "over", "into", "than", "then", "when", "just", "will", "can",
    "all", "any", "our", "its", "how", "why", "what", "are", "was", "were",
    "use", "used", "using", "get", "let", "but", "not", "yet", "even",
    "features", "pricing", "plans", "review", "overview", "platform",
    "software", "solution", "tool", "tools", "app", "apps", "product",
    "team", "teams", "users", "data", "time", "work", "workspace",
    "document", "documents", "project", "projects", "task", "tasks",
    "google", "microsoft", "apple", "amazon", "meta", "enterprise",
}

def _parse_product_names_from_text(text: str, exclude_domain: str = "", exclude_name: str = "") -> list[str]:
    """
    Heuristic extraction of SaaS product / company names from article text.
    Looks for:
      - Words with capital letters that appear next to keywords like "like", "vs", "alternative"
      - Items in numbered/bulleted lists (digit. Name or - Name)
      - Patterns like "X is a ..." or "X, a ..."
    Returns a filtered list of candidate names.
    """
    names: list[str] = []

    # Pattern 1: numbered list items — "1. Coda" / "2. ClickUp"
    list_pattern = re.findall(r"(?:^|\n)\s*\d+[\.\)]\s+([A-Z][a-zA-Z0-9\s\.\-]{1,30}?)(?:\s*[-–—\n]|\s*is\s|\s*–)", text)
    names.extend(list_pattern)

    # Pattern 2: "alternatives like X, Y, and Z"
    like_pattern = re.findall(r"(?:like|including|such as|vs\.?|versus)\s+([A-Z][a-zA-Z0-9]{2,20}(?:\s[A-Z][a-zA-Z0-9]{1,15})?)", text)
    names.extend(like_pattern)

    # Pattern 3: bold/heading-style capitalized product names (2–3 word combos starting with capital)
    cap_pattern = re.findall(r"\b([A-Z][a-zA-Z0-9]{2,15}(?:\s[A-Z][a-zA-Z0-9]{1,15})?)\b", text)
    names.extend(cap_pattern)

    # Clean up
    exclude_slug = re.sub(r"[^a-z0-9]", "", (exclude_name or "").lower())
    exclude_d_slug = (exclude_domain or "").split(".")[0].lower()

    clean: list[str] = []
    for n in names:
        n = n.strip()
        if len(n) < 3 or len(n) > 30:
            continue
        slug = re.sub(r"[^a-z0-9]", "", n.lower())
        if slug in _STOP:
            continue
        if exclude_slug and (slug == exclude_slug or exclude_slug in slug):
            continue
        if exclude_d_slug and exclude_d_slug in slug:
            continue
        # Must start with capital, be reasonably short
        if not n[0].isupper():
            continue
        clean.append(n)

    return clean


# ── Helpers ───────────────────────────────────────────────────────────────────

def _guess_company_name(url: str) -> str:
    """Derive a human-readable company name from a URL."""
    domain = _extract_domain(url)
    slug = domain.split(".")[0]
    # Title-case it and handle common patterns
    return slug.replace("-", " ").replace("_", " ").title()


def _infer_category(company_name: str) -> str:
    """Very lightweight category hint to improve search queries."""
    name = company_name.lower()
    mapping = {
        "notion": "note-taking productivity workspace",
        "figma": "design tool UI",
        "stripe": "payment processing fintech",
        "airtable": "database spreadsheet no-code",
        "slack": "team communication messaging",
        "linear": "project management issue tracking",
        "vercel": "deployment hosting frontend",
        "webflow": "no-code website builder",
        "loom": "video messaging screen recording",
    }
    for key, val in mapping.items():
        if key in name:
            return val
    return "SaaS software"


def _extract_company_name(result: dict) -> str:
    title = result.get("title", "")
    url = result.get("url", "")
    if title:
        for suffix in [" - Home", " | Home", " — ", " - Official", "| Official",
                       " - The", " | The", " · ", " - Try", " | Try"]:
            title = title.split(suffix)[0]
        return title.strip()
    return _extract_domain(url).split(".")[0].title()


def _extract_description(result: dict) -> str:
    highlights = result.get("highlights", [])
    if highlights:
        return highlights[0]
    text = result.get("text", "")
    return text[:250].strip() if text else "No description available."


def _extract_snippet(result: dict) -> str:
    highlights = result.get("highlights", [])
    if highlights:
        return highlights[0]
    text = result.get("text", "")
    return text[:200].strip() if text else ""


def _extract_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return url


def _ensure_homepage(url: str) -> str:
    """Normalize a URL to its homepage (scheme + netloc only)."""
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return url
