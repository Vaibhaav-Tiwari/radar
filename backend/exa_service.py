"""
exa_service.py — Exa AI integration for competitor discovery and intelligence.

Discovery strategy (article-mining only — findSimilar is unreliable for this use case):
  1. Search for editorial "best alternatives to X" / "X vs Y" articles
  2. Extract real product names mentioned in those articles
  3. Resolve each name to its homepage via strict domain-name matching
  4. Enrich each confirmed competitor with news, jobs, product updates
"""

import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

EXA_API_KEY = os.getenv("EXA_API_KEY", "")
EXA_BASE_URL = "https://api.exa.ai"

HEADERS = {
    "x-api-key": EXA_API_KEY,
    "Content-Type": "application/json",
    "accept": "application/json",
}

# Domains that are NEVER valid competitor homepages
_JUNK_DOMAINS = {
    "youtube.com", "wikipedia.org", "reddit.com", "twitter.com", "x.com",
    "facebook.com", "instagram.com", "linkedin.com", "medium.com",
    "techcrunch.com", "forbes.com", "businessinsider.com", "bloomberg.com",
    "crunchbase.com", "g2.com", "capterra.com", "producthunt.com",
    "trustpilot.com", "getapp.com", "alternativeto.net", "slant.co",
    "hackernews.com", "news.ycombinator.com", "github.com", "dev.to",
    "ecb.europa.eu", "cisa.gov", "gov.uk", "europa.eu",
    "coindesk.com", "cointelegraph.com", "decrypt.co",
    "investopedia.com", "nerdwallet.com", "thebalance.com",
    "getlatka.com", "tracxn.com", "pitchbook.com", "cb-insights.com",
    "businesstimes.com.sg", "businesswire.com", "prnewswire.com",
    "venturebeat.com", "siliconangle.com", "wired.com", "theverge.com",
    "beyond-notes.com", "fintech.com",
}

# Well-known competitor mappings for top products (guaranteed correct results)
_KNOWN_COMPETITORS: dict[str, list[dict]] = {
    "notion": [
        {"name": "Coda", "url": "https://coda.io"},
        {"name": "Obsidian", "url": "https://obsidian.md"},
        {"name": "ClickUp", "url": "https://clickup.com"},
        {"name": "Confluence", "url": "https://www.atlassian.com/software/confluence"},
        {"name": "Evernote", "url": "https://evernote.com"},
        {"name": "Slite", "url": "https://slite.com"},
    ],
    "stripe": [
        {"name": "Braintree", "url": "https://www.braintreepayments.com"},
        {"name": "Adyen", "url": "https://www.adyen.com"},
        {"name": "Square", "url": "https://squareup.com"},
        {"name": "Checkout.com", "url": "https://www.checkout.com"},
        {"name": "Paddle", "url": "https://www.paddle.com"},
        {"name": "Chargebee", "url": "https://www.chargebee.com"},
    ],
    "figma": [
        {"name": "Sketch", "url": "https://www.sketch.com"},
        {"name": "Adobe XD", "url": "https://adobexd.com"},
        {"name": "Framer", "url": "https://www.framer.com"},
        {"name": "InVision", "url": "https://www.invisionapp.com"},
        {"name": "Penpot", "url": "https://penpot.app"},
        {"name": "Marvel", "url": "https://marvelapp.com"},
    ],
    "slack": [
        {"name": "Microsoft Teams", "url": "https://www.microsoft.com/en-us/microsoft-teams"},
        {"name": "Discord", "url": "https://discord.com"},
        {"name": "Lark", "url": "https://www.larksuite.com"},
        {"name": "Mattermost", "url": "https://mattermost.com"},
        {"name": "Twist", "url": "https://twist.com"},
        {"name": "Google Chat", "url": "https://chat.google.com"},
    ],
    "linear": [
        {"name": "Jira", "url": "https://www.atlassian.com/software/jira"},
        {"name": "Asana", "url": "https://asana.com"},
        {"name": "ClickUp", "url": "https://clickup.com"},
        {"name": "Height", "url": "https://height.app"},
        {"name": "Shortcut", "url": "https://shortcut.com"},
        {"name": "Plane", "url": "https://plane.so"},
    ],
    "airtable": [
        {"name": "Smartsheet", "url": "https://www.smartsheet.com"},
        {"name": "Monday.com", "url": "https://monday.com"},
        {"name": "Baserow", "url": "https://baserow.io"},
        {"name": "NocoDB", "url": "https://nocodb.com"},
        {"name": "Rows", "url": "https://rows.com"},
        {"name": "Retool", "url": "https://retool.com"},
    ],
    "coinbase": [
        {"name": "Kraken", "url": "https://www.kraken.com"},
        {"name": "Binance", "url": "https://www.binance.com"},
        {"name": "Gemini", "url": "https://www.gemini.com"},
        {"name": "Crypto.com", "url": "https://crypto.com"},
        {"name": "Bitfinex", "url": "https://www.bitfinex.com"},
        {"name": "OKX", "url": "https://www.okx.com"},
    ],
    "hubspot": [
        {"name": "Salesforce", "url": "https://www.salesforce.com"},
        {"name": "Pipedrive", "url": "https://www.pipedrive.com"},
        {"name": "Zoho CRM", "url": "https://www.zoho.com/crm"},
        {"name": "Close", "url": "https://close.com"},
        {"name": "Attio", "url": "https://attio.com"},
        {"name": "ActiveCampaign", "url": "https://www.activecampaign.com"},
    ],
    "vercel": [
        {"name": "Netlify", "url": "https://www.netlify.com"},
        {"name": "Render", "url": "https://render.com"},
        {"name": "Railway", "url": "https://railway.app"},
        {"name": "Fly.io", "url": "https://fly.io"},
        {"name": "Cloudflare Pages", "url": "https://pages.cloudflare.com"},
        {"name": "AWS Amplify", "url": "https://aws.amazon.com/amplify"},
    ],
    "intercom": [
        {"name": "Zendesk", "url": "https://www.zendesk.com"},
        {"name": "Freshdesk", "url": "https://www.freshworks.com/freshdesk"},
        {"name": "Crisp", "url": "https://crisp.chat"},
        {"name": "Drift", "url": "https://www.drift.com"},
        {"name": "Help Scout", "url": "https://www.helpscout.com"},
        {"name": "Front", "url": "https://front.com"},
    ],
    "loom": [
        {"name": "Vidyard", "url": "https://www.vidyard.com"},
        {"name": "Wistia", "url": "https://wistia.com"},
        {"name": "Vimeo", "url": "https://vimeo.com"},
        {"name": "Scribe", "url": "https://scribehow.com"},
        {"name": "Tella", "url": "https://www.tella.tv"},
        {"name": "Screenpal", "url": "https://screenpal.com"},
    ],
    "shopify": [
        {"name": "BigCommerce", "url": "https://www.bigcommerce.com"},
        {"name": "WooCommerce", "url": "https://woocommerce.com"},
        {"name": "Wix", "url": "https://www.wix.com"},
        {"name": "Squarespace", "url": "https://www.squarespace.com"},
        {"name": "Ecwid", "url": "https://www.ecwid.com"},
        {"name": "Magento", "url": "https://business.adobe.com/products/magento"},
    ],
}


# ── Master discovery ──────────────────────────────────────────────────────────

async def discover_competitors(target_url: str, num_results: int = 6) -> list[dict]:
    """
    Discover real competitors for a given company URL.
    Priority:
      1. Known competitor list (instant, zero API calls, always correct)
      2. Article mining via Exa neural search (for unlisted companies)
    """
    company_name = _guess_company_name(target_url)
    name_key = company_name.lower().strip()

    # Check known list first
    for key, competitors in _KNOWN_COMPETITORS.items():
        if key in name_key or name_key in key:
            # Enrich with descriptions asynchronously
            return await _enrich_known_competitors(competitors[:num_results])

    # Fallback: article mining
    return await _discover_via_articles(company_name, target_url, num_results)


async def _enrich_known_competitors(competitors: list[dict]) -> list[dict]:
    """Fetch short descriptions for known competitors via Exa."""
    async def fetch_desc(c: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{EXA_BASE_URL}/search",
                    headers=HEADERS,
                    json={
                        "query": f"{c['name']} product description what is",
                        "numResults": 1,
                        "type": "keyword",
                        "contents": {
                            "text": {"maxCharacters": 300},
                            "highlights": {"numSentences": 2, "highlightsPerUrl": 1},
                        },
                    },
                )
                resp.raise_for_status()
                results = resp.json().get("results", [])
                if results:
                    c["description"] = _extract_description(results[0])
                else:
                    c["description"] = ""
        except Exception:
            c["description"] = ""
        return c

    return list(await asyncio.gather(*[fetch_desc(c) for c in competitors]))


async def _discover_via_articles(
    company_name: str, target_url: str, num_results: int
) -> list[dict]:
    """
    Article-mining fallback for companies not in the known list.
    Searches for comparison/alternatives articles, extracts product names,
    resolves each to a homepage with strict domain-name matching.
    """
    domain = _extract_domain(target_url)
    category = _infer_category(company_name, target_url)

    queries = [
        f"best alternatives to {company_name} {category}",
        f"{company_name} competitors {category} comparison 2024 2025",
        f"top {category} tools like {company_name}",
        f"{company_name} vs alternatives software",
    ]

    candidate_names: list[str] = []

    async with httpx.AsyncClient(timeout=30) as client:
        tasks = []
        for query in queries:
            tasks.append(_search_articles(client, query, domain))
        results_list = await asyncio.gather(*tasks)

    for names in results_list:
        candidate_names.extend(names)

    # Deduplicate
    seen: set[str] = set()
    unique_names: list[str] = []
    for n in candidate_names:
        k = n.lower()
        if k not in seen and k != company_name.lower():
            seen.add(k)
            unique_names.append(n)

    # Resolve names → homepages (strict)
    resolved = await asyncio.gather(*[_resolve_homepage_strict(n) for n in unique_names[:16]])
    companies = [c for c in resolved if c]

    # Deduplicate by domain, filter junk
    seen_domains: set[str] = {_extract_domain(target_url)}
    final: list[dict] = []
    for c in companies:
        d = _extract_domain(c["url"])
        if d in seen_domains or d in _JUNK_DOMAINS:
            continue
        seen_domains.add(d)
        final.append(c)
        if len(final) >= num_results:
            break

    return final


async def _search_articles(client: httpx.AsyncClient, query: str, exclude_domain: str) -> list[str]:
    """Run one article search and return extracted product names."""
    try:
        resp = await client.post(
            f"{EXA_BASE_URL}/search",
            headers=HEADERS,
            json={
                "query": query,
                "numResults": 3,
                "useAutoprompt": True,
                "type": "neural",
                "excludeDomains": [exclude_domain],
                "contents": {"text": {"maxCharacters": 4000}},
            },
        )
        resp.raise_for_status()
        names = []
        for r in resp.json().get("results", []):
            names.extend(_parse_product_names(r.get("text", "") + "\n" + r.get("title", "")))
        return names
    except Exception:
        return []


async def _resolve_homepage_strict(company_name: str) -> dict | None:
    """
    Resolve a company name to its homepage.
    STRICT: result domain must contain the company name slug.
    Returns None rather than a wrong URL.
    """
    name_slug = re.sub(r"[^a-z0-9]", "", company_name.lower())
    if len(name_slug) < 2:
        return None

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.post(
                f"{EXA_BASE_URL}/search",
                headers=HEADERS,
                json={
                    "query": f"{company_name} official website",
                    "numResults": 5,
                    "useAutoprompt": False,
                    "type": "keyword",
                    "contents": {
                        "text": {"maxCharacters": 400},
                        "highlights": {"numSentences": 2, "highlightsPerUrl": 1},
                    },
                },
            )
            resp.raise_for_status()
            for r in resp.json().get("results", []):
                url = r.get("url", "")
                domain = _extract_domain(url)
                if domain in _JUNK_DOMAINS:
                    continue
                domain_slug = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])
                # Accept if name slug is contained in domain slug or vice versa (min 3 chars)
                overlap = len(name_slug) >= 3 and (name_slug in domain_slug or domain_slug in name_slug)
                if overlap:
                    return {
                        "name": company_name,
                        "url": _ensure_homepage(url),
                        "description": _extract_description(r),
                    }
        except Exception:
            pass
    return None


# ── Enrichment ────────────────────────────────────────────────────────────────

async def get_recent_news(company_name: str, company_url: str) -> list[dict]:
    """Recent news and funding rounds — must mention the company name."""
    domain = _extract_domain(company_url)
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{EXA_BASE_URL}/search",
                headers=HEADERS,
                json={
                    "query": f"{company_name} funding raise announcement news 2024 2025",
                    "numResults": 4,
                    "useAutoprompt": True,
                    "type": "neural",
                    "excludeDomains": [domain],
                    "contents": {
                        "text": {"maxCharacters": 400},
                        "highlights": {"numSentences": 2, "highlightsPerUrl": 1},
                    },
                },
            )
            resp.raise_for_status()
            articles = []
            name_lower = company_name.lower()
            for r in resp.json().get("results", []):
                title = r.get("title", "")
                text = r.get("text", "")[:300]
                # Must mention company name
                if name_lower not in title.lower() and name_lower not in text.lower():
                    continue
                src = _extract_domain(r.get("url", ""))
                if src in _JUNK_DOMAINS and src not in {
                    "techcrunch.com", "bloomberg.com", "forbes.com",
                    "businesswire.com", "venturebeat.com",
                }:
                    continue
                articles.append({
                    "title": title,
                    "url": r.get("url", ""),
                    "snippet": _extract_snippet(r),
                    "published_date": r.get("publishedDate", ""),
                    "source": src,
                })
            return articles[:3]
        except Exception:
            return []


async def get_job_signals(company_name: str) -> list[dict]:
    """Hiring signals scoped strictly to the company's own job listings."""
    name_slug = re.sub(r"[^a-z0-9]", "", company_name.lower())
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{EXA_BASE_URL}/search",
                headers=HEADERS,
                json={
                    "query": f"{company_name} jobs careers hiring",
                    "numResults": 5,
                    "useAutoprompt": False,
                    "type": "keyword",
                    "includeDomains": [
                        "linkedin.com", "greenhouse.io", "lever.co",
                        "jobs.ashbyhq.com", "wellfound.com",
                        "boards.greenhouse.io", "jobs.lever.co",
                        "apply.workable.com", "careers.smartrecruiters.com",
                    ],
                    "contents": {"text": {"maxCharacters": 200}},
                },
            )
            resp.raise_for_status()
            jobs = []
            for r in resp.json().get("results", []):
                title = r.get("title", "Open Role")
                url = r.get("url", "")
                url_slug = re.sub(r"[^a-z0-9]", "", url.lower())
                title_slug = re.sub(r"[^a-z0-9]", "", title.lower())
                # Must mention company in URL or title
                if name_slug not in url_slug and name_slug not in title_slug:
                    continue
                jobs.append({
                    "title": title,
                    "url": url,
                    "source": _extract_domain(url),
                    "published_date": r.get("publishedDate", ""),
                })
            return jobs[:3]
        except Exception:
            return []


async def get_product_updates(company_name: str, company_url: str) -> list[dict]:
    """Product launches, feature announcements — must mention the company."""
    domain = _extract_domain(company_url)
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{EXA_BASE_URL}/search",
                headers=HEADERS,
                json={
                    "query": f"{company_name} new feature launch update release 2024 2025",
                    "numResults": 4,
                    "useAutoprompt": True,
                    "type": "neural",
                    "contents": {
                        "text": {"maxCharacters": 400},
                        "highlights": {"numSentences": 2, "highlightsPerUrl": 1},
                    },
                },
            )
            resp.raise_for_status()
            updates = []
            name_lower = company_name.lower()
            for r in resp.json().get("results", []):
                title = r.get("title", "")
                text = r.get("text", "")[:300]
                if name_lower not in title.lower() and name_lower not in text.lower():
                    continue
                updates.append({
                    "title": title,
                    "url": r.get("url", ""),
                    "snippet": _extract_snippet(r),
                    "source": _extract_domain(r.get("url", "")),
                    "published_date": r.get("publishedDate", ""),
                })
            return updates[:3]
        except Exception:
            return []


# ── Text parsing ──────────────────────────────────────────────────────────────

_STOP_WORDS = {
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
    "however", "overall", "another", "section", "while", "these", "those",
    "which", "where", "there", "here", "about", "after", "before",
    "read", "start", "try", "sign", "learn", "explore", "discover",
    "alternatives", "alternative", "competitors", "competitor", "comparison",
    "versus", "between", "similar", "instead",
}


def _parse_product_names(text: str) -> list[str]:
    """Extract SaaS product names from article text."""
    names: list[str] = []

    # Numbered list items: "1. Coda —" or "2. ClickUp is"
    names += re.findall(
        r"(?:^|\n)\s*\d+[\.\)]\s+([A-Z][a-zA-Z0-9][a-zA-Z0-9\s\.\-]{0,20}?)(?:\s*[-–—:\n]|\s+is\s|\s+–)",
        text,
    )
    # Markdown headings: "## Coda" or "### ClickUp"
    names += re.findall(r"^#{1,4}\s+([A-Z][a-zA-Z0-9][a-zA-Z0-9\s]{1,20}?)(?:\s*\n|$)", text, re.MULTILINE)
    # "alternatives like X, Y, Z"
    names += re.findall(
        r"(?:like|including|such as|alternatives?\s+(?:include|are|:))\s+"
        r"([A-Z][a-zA-Z0-9]{2,18}(?:\s[A-Z][a-zA-Z0-9]{1,12})?)",
        text,
    )
    # "X vs Y"
    for a, b in re.findall(r"\b([A-Z][a-zA-Z0-9]{2,18})\s+vs\.?\s+([A-Z][a-zA-Z0-9]{2,18})\b", text):
        names += [a, b]

    clean: list[str] = []
    for n in names:
        n = n.strip().rstrip(".-–—:")
        if len(n) < 2 or len(n) > 28:
            continue
        if re.sub(r"[^a-z]", "", n.lower()) in _STOP_WORDS:
            continue
        if not n[0].isupper():
            continue
        if re.match(r"^\d+$", n):
            continue
        clean.append(n)

    return clean


# ── Helpers ───────────────────────────────────────────────────────────────────

def _guess_company_name(url: str) -> str:
    domain = _extract_domain(url)
    slug = domain.split(".")[0]
    return slug.replace("-", " ").replace("_", " ").title()


def _infer_category(company_name: str, company_url: str = "") -> str:
    name = company_name.lower()
    domain = _extract_domain(company_url).lower()
    combined = name + " " + domain

    rules = [
        (["notion", "obsidian", "roam", "bear", "logseq", "coda", "confluence", "slite"],
         "note-taking knowledge management workspace"),
        (["stripe", "braintree", "adyen", "square", "checkout", "paddle", "chargebee"],
         "payment processing fintech"),
        (["figma", "sketch", "framer", "invision", "canva", "penpot"],
         "design tool UI prototyping"),
        (["airtable", "smartsheet", "baserow", "nocodb", "rows"],
         "database spreadsheet no-code"),
        (["slack", "discord", "lark", "mattermost", "twist"],
         "team communication messaging"),
        (["linear", "jira", "asana", "monday", "clickup", "trello", "height", "shortcut"],
         "project management issue tracking"),
        (["vercel", "netlify", "render", "railway", "fly"],
         "frontend deployment hosting"),
        (["webflow", "bubble", "wix", "squarespace", "framer"],
         "no-code website builder"),
        (["loom", "vidyard", "wistia", "vimeo", "tella"],
         "video messaging screen recording"),
        (["hubspot", "salesforce", "pipedrive", "close", "attio"],
         "CRM sales software"),
        (["coinbase", "kraken", "binance", "gemini", "crypto"],
         "cryptocurrency exchange trading"),
        (["shopify", "bigcommerce", "woocommerce", "ecwid"],
         "e-commerce platform"),
        (["intercom", "zendesk", "freshdesk", "crisp", "drift"],
         "customer support helpdesk"),
        (["datadog", "newrelic", "grafana", "dynatrace"],
         "observability monitoring devops"),
        (["openai", "anthropic", "cohere", "mistral"],
         "AI language model API"),
    ]

    for keywords, category in rules:
        if any(kw in combined for kw in keywords):
            return category

    return "SaaS B2B software productivity"


def _extract_company_name(result: dict) -> str:
    title = result.get("title", "")
    url = result.get("url", "")
    if title:
        for sep in [" - ", " | ", " — ", " · ", " – "]:
            parts = title.split(sep)
            if len(parts) > 1:
                candidate = min(parts, key=len).strip()
                if len(candidate) >= 2:
                    return candidate
        return title.strip()
    return _extract_domain(url).split(".")[0].title()


def _extract_description(result: dict) -> str:
    highlights = result.get("highlights", [])
    if highlights:
        return highlights[0]
    text = result.get("text", "")
    return text[:250].strip() if text else ""


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
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return url
