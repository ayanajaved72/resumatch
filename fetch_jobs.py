import os
import requests
import sqlite3
import bs4
from bs4 import BeautifulSoup
import html
import json
import time
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

client = Groq()

CRITERIA = "Summer 2027 internship, SWE/AI/automation field, skills in Java, Python, C/C++, OOP, APIs, Claude API"

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
EMAIL_FROM = os.environ.get("EMAIL_FROM")
EMAIL_TO = os.environ.get("EMAIL_TO")


def clean_html(raw_html):
    unescaped = html.unescape(raw_html)
    soup = BeautifulSoup(unescaped, "html.parser")
    return soup.get_text(separator="\n")


def normalize_job(job, slug):
    return {
        "source": "greenhouse",
        "slug": slug,
        "company": job["company_name"],
        "title": job["title"],
        "location": job["location"]["name"],
        "url": job["absolute_url"],
        "job_id": job["id"],
        "updated_at": job["updated_at"],
    }


def score_job(job, description, criteria):
    prompt = f"""You are screening a job posting for a candidate.

Candidate criteria:
{criteria}

Job posting:
Title: {job['title']}
Company: {job['company']}
Location: {job['location']}
Description:
{description}

Judge whether this posting is a genuine fit. It must be an internship specifically for Summer 2027 in software engineering, AI, or a closely related technical field. Reject anything that is a full-time/new-grad role, a different internship season/year, or an unrelated field (e.g. sales, operations, warehouse).

Respond ONLY with valid JSON in this exact format, no other text, no markdown code fences:
{{"is_match": true or false, "fit_score": 1-10, "reasoning": "one sentence"}}
"""
    response = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="openai/gpt-oss-20b",
    )
    return response.choices[0].message.content


def get_title_key(title):
    return title.split(" - ")[0].strip()


def send_email_summary(matches, total_scored):
    """Emails a summary of this run's matches via Resend. No-ops if not configured."""
    if not (RESEND_API_KEY and EMAIL_FROM and EMAIL_TO):
        print(
            "Email not configured (missing RESEND_API_KEY / EMAIL_FROM / EMAIL_TO) — skipping email."
        )
        return

    if matches:
        rows = "".join(f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;">
                    <a href="{m['url']}">{m['title']}</a>
                </td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{m['company']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{m['fit_score']}/10</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{m['reasoning']}</td>
            </tr>
            """ for m in matches)
        body_html = f"""
        <h2>Resumatch weekly run</h2>
        <p>Scored {total_scored} new postings — {len(matches)} matched your criteria.</p>
        <table style="border-collapse:collapse;width:100%;">
            <tr style="text-align:left;">
                <th style="padding:8px;border-bottom:2px solid #333;">Title</th>
                <th style="padding:8px;border-bottom:2px solid #333;">Company</th>
                <th style="padding:8px;border-bottom:2px solid #333;">Fit</th>
                <th style="padding:8px;border-bottom:2px solid #333;">Why</th>
            </tr>
            {rows}
        </table>
        """
    else:
        body_html = f"""
        <h2>Resumatch weekly run</h2>
        <p>Scored {total_scored} new postings — no matches this week.</p>
        """

    response = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": EMAIL_FROM,
            "to": [EMAIL_TO],
            "subject": f"Resumatch: {len(matches)} new matches this week",
            "html": body_html,
        },
    )

    if response.status_code >= 300:
        print(f"Email failed to send ({response.status_code}): {response.text}")
    else:
        print("Email summary sent.")


# ---- Fetch and normalize from all companies ----

company_slugs = ["coinbase", "airbnb", "robinhood", "anthropic"]

normalized_jobs = []

for slug in company_slugs:
    print(f"Fetching jobs from: {slug}")
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    response = requests.get(url)
    data = response.json()

    for job in data["jobs"]:
        normalized_jobs.append(normalize_job(job, slug))

print(f"Total jobs fetched: {len(normalized_jobs)}")

# ---- Store in database ----

conn = sqlite3.connect("jobs.db")
cursor = conn.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        job_id INTEGER PRIMARY KEY,
        source TEXT,
        slug TEXT,
        company TEXT,
        title TEXT,
        location TEXT,
        url TEXT,
        updated_at TEXT
    )
""")
conn.commit()

for col, col_type in [
    ("slug", "TEXT"),
    ("is_match", "INTEGER"),
    ("fit_score", "INTEGER"),
    ("reasoning", "TEXT"),
]:
    try:
        cursor.execute(f"ALTER TABLE jobs ADD COLUMN {col} {col_type}")
    except sqlite3.OperationalError:
        pass
conn.commit()

for job in normalized_jobs:
    cursor.execute(
        """
        INSERT OR IGNORE INTO jobs (job_id, source, slug, company, title, location, url, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            job["job_id"],
            job["source"],
            job["slug"],
            job["company"],
            job["title"],
            job["location"],
            job["url"],
            job["updated_at"],
        ),
    )
conn.commit()

cursor.execute("UPDATE jobs SET slug = 'doordashusa' WHERE slug IS NULL")
conn.commit()

# ---- Build title cache from already-scored jobs ----

cursor.execute(
    "SELECT title, is_match, fit_score, reasoning FROM jobs WHERE fit_score IS NOT NULL"
)
already_scored = cursor.fetchall()

title_cache = {}
for row in already_scored:
    title, is_match, fit_score, reasoning = row
    key = get_title_key(title)
    title_cache[key] = {
        "is_match": bool(is_match),
        "fit_score": fit_score,
        "reasoning": reasoning,
    }

# ---- Score unscored jobs ----

MAX_JOBS_PER_RUN = 500  # caps how many postings get processed in one run

cursor.execute(
    "SELECT job_id, slug, title, company, location, url FROM jobs WHERE fit_score IS NULL"
)
unscored_jobs = cursor.fetchall()[:MAX_JOBS_PER_RUN]

matches = []

for row in unscored_jobs:
    job_id, slug, title, company, location, url = row
    job = {
        "job_id": job_id,
        "title": title,
        "company": company,
        "location": location,
        "url": url,
    }

    key = get_title_key(title)
    was_cached = key in title_cache

    if was_cached:
        print(f"Skipping (cached): {title}")
        parsed = title_cache[key]
    else:
        print(f"Scoring: {title}")
        detail_url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
        detail_response = requests.get(detail_url)
        detail_data = detail_response.json()

        if "content" not in detail_data:
            print(f"  Skipping — no content returned for job_id {job_id}")
            continue

        description = clean_html(detail_data["content"])[:1500]

        result = score_job(job, description, CRITERIA)
        parsed = json.loads(result)
        title_cache[key] = parsed

    cursor.execute(
        "UPDATE jobs SET is_match = ?, fit_score = ?, reasoning = ? WHERE job_id = ?",
        (int(parsed["is_match"]), parsed["fit_score"], parsed["reasoning"], job_id),
    )
    conn.commit()

    if parsed["is_match"]:
        matches.append({**job, **parsed})

    if not was_cached:
        time.sleep(5)

print(f"Found {len(matches)} matches out of {len(unscored_jobs)} unscored jobs")
for match in matches:
    print(
        match["title"],
        "-",
        match["company"],
        "-",
        match["fit_score"],
        "-",
        match["reasoning"],
    )

# ---- Email summary ----

send_email_summary(matches, len(unscored_jobs))
