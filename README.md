# Resumatch

A weekly pipeline that pulls internship postings directly from company job boards and scores each one against my own criteria with an LLM instead of relying on a generic job board's search, which kept surfacing postings that didn't actually fit what I was looking for.

## What it does

1. **Fetches** postings from multiple companies' public Greenhouse job APIs (currently Coinbase, Airbnb, Robinhood, and Anthropic).
2. **Parses** each posting's HTML description with `html.unescape()` + BeautifulSoup to get clean text.
3. **Dedupes via a title cache** — many postings are the same role reposted across locations (e.g. "Shift Lead – Arlington" vs. "Shift Lead – Deer Park"). Resumatch normalizes each title to a shared key and reuses a cached score instead of re-scoring identical roles, which cuts down on redundant (and inconsistent) LLM calls.
4. **Scores** new postings against my criteria using an LLM hosted on Groq, returning a match verdict, a 1–10 fit score, and a one-sentence reason.
5. **Stores** everything in a local SQLite database (`jobs.db`).
6. **Emails a summary** of that run's matches so I don't have to manually query the database to see what came back.

## Architecture

```
Greenhouse APIs → Fetch & Parse → Title key cached?
                                       │
                          ┌────────────┴────────────┐
                         no                         yes
                          │                           │
                     LLM Scoring              Reuse Cached Score
                      (via Groq)               (skips LLM call)
                          │                           │
                          └────────────┬──────────────┘
                                       ▼
                             Ranked Results (SQLite)
                                       │
                                       ▼
                                Email Summary
```

## Stack

- **Python** — `requests`, `BeautifulSoup`, `sqlite3`
- **Groq** — LLM inference for scoring (switched to Groq from a paid API to cut cost, since this runs weekly against hundreds of postings)
- **Resend** — sends the weekly email summary
- **SQLite** — stores every fetched posting plus its score, so re-runs only score what's new

## Setup

1. Clone the repo and install dependencies:
   ```bash
   pip install requests beautifulsoup4 groq python-dotenv
   ```
2. Create a `.env` file in the project root:
   ```
   GROQ_API_KEY=your_groq_api_key
   RESEND_API_KEY=your_resend_api_key
   EMAIL_FROM="Resumatch <resumatch@yourdomain.com>"
   EMAIL_TO=you@example.com
   ```
   - `GROQ_API_KEY` is read automatically by the Groq client.
   - Resend requires a verified sending domain for `EMAIL_FROM` in production; for quick testing you can send from their sandbox address (`onboarding@resend.dev`) to your own verified email.
   - If the Resend variables aren't set, the script just skips sending an email and prints a note. It won't break the rest of the run.
3. Run it:
   ```bash
   python fetch_jobs.py
   ```
4. Schedule it (e.g. weekly via `cron` or a GitHub Action) so it fetches, scores, and emails automatically.

## Metrics

- Processes ~500 postings per run, run weekly (~2,000/month) across each company's Greenhouse board
- In a typical run, ~12 postings have 5–12 duplicate location-variants each, all caught by the title cache before they'd otherwise hit the LLM

## What I learned

This was my first project where AI did something end-to-end: automating a real weekly task rather than just generating content in response to a single prompt. Most of what makes a pipeline like this reliable isn't the model call itself, but rather the unglamorous data-cleaning, deduplication, and validation work around it. I also had to build around real-world instability along the way: a paid LLM provider that got too expensive to run weekly, a false "credits depleted" error from one provider, and a Groq model that got deprecated mid-build.

## Next steps

- Expand to more companies' job boards
- Add a simple interface for browsing scored results instead of querying SQLite directly
- Track scoring accuracy over time