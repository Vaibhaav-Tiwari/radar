# Competitive Intelligence Radar

> Enter a company URL. Get a live competitive landscape — news, hiring signals, and product moves — in seconds.

Built with [Exa AI](https://exa.ai) (similarity search + neural search), FastAPI, and a custom high-end frontend.

---

## Stack

| Layer       | Tech                                      |
|-------------|-------------------------------------------|
| API Backend | FastAPI + Uvicorn (Python)                |
| AI Search   | Exa AI — findSimilar + neural search      |
| Frontend    | Vanilla HTML/CSS/JS (no framework needed) |
| Fonts       | Cormorant Garamond + DM Sans + DM Mono    |

---

## Setup

### 1. Clone & configure

```bash
git clone <your-repo>
cd radar
cp .env.example .env
```

Edit `.env`:
```
EXA_API_KEY=your_exa_api_key_here
```

Get your Exa key at [exa.ai](https://exa.ai) — free tier available.

### 2. Install & run

```bash
chmod +x run.sh
./run.sh
```

Open [http://localhost:8000](http://localhost:8000).

---

## How it works

1. **Competitor Discovery** — Exa's `findSimilar` endpoint finds companies with similar web content to the target URL. This is Exa's killer feature: semantic URL similarity, not keyword matching.

2. **News Signals** — Neural search for `{company} latest news funding product launch` — surfaces funding rounds, press coverage, major announcements.

3. **Hiring Signals** — Keyword search on LinkedIn, Greenhouse, Lever, Ashby, and Wellfound for open roles — indicates team growth direction.

4. **Product Updates** — Neural search for changelogs, release notes, and feature launches — shows where the product is heading.

All four searches run in parallel per competitor (asyncio.gather), keeping total latency low.

---

## Project Structure

```
radar/
├── backend/
│   ├── main.py          # FastAPI app + routes
│   └── exa_service.py   # All Exa API calls (modular)
├── frontend/
│   └── templates/
│       └── index.html   # Single-file frontend
├── .env.example         # Environment template
├── requirements.txt
├── run.sh               # One-command startup
└── README.md
```

---

## API Endpoints

| Method | Path         | Description                              |
|--------|--------------|------------------------------------------|
| GET    | `/`          | Serve the frontend                       |
| POST   | `/api/scan`  | Run competitive analysis for a URL       |
| GET    | `/api/health`| Check server + API key status            |

### POST /api/scan

```json
// Request
{ "url": "stripe.com" }

// Response
{
  "target_url": "https://stripe.com",
  "competitors": [
    {
      "name": "Braintree",
      "url": "https://www.braintreepayments.com",
      "description": "...",
      "news": [...],
      "jobs": [...],
      "product_updates": [...]
    }
  ]
}
```

---

## Design Philosophy

The frontend takes inspiration from high-end editorial design — warm amber and ink tones, Cormorant Garamond as the display serif, DM Mono for data labels. The goal is to look like a premium B2B product rather than a hackathon prototype.
