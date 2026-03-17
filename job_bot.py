import html
import json
import os
import re
import sqlite3
import time
from urllib.parse import urljoin

import requests

# Pull secrets from environment variables (crucial for GitHub Actions)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

JOB_QUERY = os.environ.get("JOB_QUERY", "python developer")
JOB_LOCATION = os.environ.get("JOB_LOCATION", "India")
REQUEST_TIMEOUT_SECONDS = 25

try:
    MAX_JOBS_PER_SOURCE = max(5, min(int(os.environ.get("MAX_JOBS_PER_SOURCE", "25")), 100))
except ValueError:
    MAX_JOBS_PER_SOURCE = 25

TARGET_SKILLS = [
    "python",
    "javascript",
    "react",
    "node.js",
    "express",
    "mysql",
    "mongodb",
    "php",
    "opencv",
    "tensorflow",
    "mediapipe",
    "langchain",
    "full-stack",
    "backend",
    "frontend",
    "ai",
    "computer vision",
]

COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def clean_text(value):
    """Normalizes whitespace and HTML entities."""
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_html_tags(value):
    """Removes HTML tags from text."""
    return re.sub(r"<[^>]+>", " ", value or "")


def slugify(value):
    """Converts a text to a URL-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", clean_text(value).lower())
    return slug.strip("-")


def skill_matches(skill, haystack):
    """Matches a skill with proper boundaries for plain words."""
    skill_lc = skill.lower()
    if re.search(r"[^a-z0-9 ]", skill_lc):
        return skill_lc in haystack
    return re.search(rf"\b{re.escape(skill_lc)}\b", haystack) is not None


def find_matched_skills(job):
    """Returns matched skills for a job dictionary."""
    haystack = " ".join(
        [
            job.get("title", ""),
            job.get("company", ""),
            job.get("location", ""),
            job.get("description", ""),
            job.get("skills", ""),
        ]
    ).lower()
    return [skill for skill in TARGET_SKILLS if skill_matches(skill, haystack)]


def build_job_key(job):
    """Builds a stable unique key for dedupe storage."""
    identifier = clean_text(job.get("id")) or clean_text(job.get("link"))
    return f"{job.get('source', 'Unknown')}|{identifier}"


def init_db():
    """Creates the database and table if they don't exist."""
    conn = sqlite3.connect("jobs.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_jobs (
            link TEXT PRIMARY KEY
        )
        """
    )
    conn.commit()
    return conn


def send_telegram_alert(session, job, matched_skills):
    """Pushes a formatted job alert to Telegram."""
    if not BOT_TOKEN or not CHAT_ID:
        print("BOT_TOKEN or CHAT_ID is missing; skipping Telegram send.")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    title = html.escape(clean_text(job.get("title", "Untitled role")))
    source = html.escape(clean_text(job.get("source", "Unknown")))
    company = html.escape(clean_text(job.get("company", "Unknown")))
    location = html.escape(clean_text(job.get("location", "Unknown")))
    link = html.escape(clean_text(job.get("link", "")))
    matched = html.escape(", ".join(matched_skills))

    message = (
        "<b>New Job Match</b>\n\n"
        f"<b>Source:</b> {source}\n"
        f"<b>Role:</b> {title}\n"
        f"<b>Company:</b> {company}\n"
        f"<b>Location:</b> {location}\n"
        f"<b>Matched Skills:</b> {matched}\n"
        f"<b>Link:</b> {link}"
    )
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}

    try:
        response = session.post(url, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        if response.status_code != 200:
            print(f"Failed to send alert: {response.status_code} - {response.text}")
        return response.status_code == 200
    except Exception as exc:
        print(f"Exception sending alert: {exc}")
        return False


def scrape_indeed(session, query, location, limit):
    """
    Best-effort scrape for Indeed.
    Often blocked by anti-bot 403 checks.
    """
    params = {"q": query, "l": location}
    response = session.get(
        "https://in.indeed.com/jobs",
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        print(f"Indeed unavailable (status {response.status_code}).")
        return []

    links = re.findall(r'<a[^>]+href="(/viewjob\?[^\"]+)"', response.text, re.I)
    titles = re.findall(
        r'<h2[^>]*jobTitle[^>]*>.*?<a[^>]*>(.*?)</a>',
        response.text,
        re.I | re.S,
    )
    jobs = []
    for idx in range(min(len(links), len(titles), limit)):
        link = urljoin("https://in.indeed.com", html.unescape(links[idx]))
        jobs.append(
            {
                "source": "Indeed",
                "id": link,
                "title": clean_text(strip_html_tags(titles[idx])),
                "company": "",
                "location": location,
                "link": link,
                "description": "",
                "skills": "",
            }
        )
    return jobs


def scrape_linkedin(session, query, location, limit):
    """Scrapes public LinkedIn guest jobs search results."""
    response = session.get(
        "https://www.linkedin.com/jobs/search/",
        params={"keywords": query, "location": location},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        print(f"LinkedIn unavailable (status {response.status_code}).")
        return []

    links = re.findall(
        r'<a class="base-card__full-link[^\"]*" href="([^"]+)"',
        response.text,
        re.I,
    )
    titles = [
        clean_text(strip_html_tags(value))
        for value in re.findall(
            r'<h3 class="base-search-card__title[^\"]*">\s*(.*?)\s*</h3>',
            response.text,
            re.I | re.S,
        )
    ]
    companies = [
        clean_text(strip_html_tags(value))
        for value in re.findall(
            r'<h4 class="base-search-card__subtitle[^\"]*">\s*(?:<a[^>]*>)?\s*(.*?)\s*(?:</a>)?\s*</h4>',
            response.text,
            re.I | re.S,
        )
    ]
    locations = [
        clean_text(strip_html_tags(value))
        for value in re.findall(
            r'<span class="job-search-card__location[^\"]*">\s*(.*?)\s*</span>',
            response.text,
            re.I | re.S,
        )
    ]

    jobs = []
    seen_links = set()
    for idx in range(min(len(links), len(titles))):
        link = clean_text(links[idx])
        if not link or link in seen_links:
            continue

        seen_links.add(link)
        match = re.search(r"/jobs/view/[^/]*-(\d+)", link)
        jobs.append(
            {
                "source": "LinkedIn",
                "id": match.group(1) if match else link,
                "title": titles[idx] or "Untitled role",
                "company": companies[idx] if idx < len(companies) else "",
                "location": locations[idx] if idx < len(locations) else location,
                "link": link,
                "description": "",
                "skills": "",
            }
        )
        if len(jobs) >= limit:
            break

    return jobs


def scrape_naukri(session, query, location, limit):
    """Fetches jobs using Naukri public search API."""
    query_slug = slugify(query) or "python-developer"
    params = {
        "noOfResults": str(limit),
        "urlType": "search_by_keyword",
        "searchType": "adv",
        "routeKeyword": query_slug,
        "keyword": query_slug,
        "pageNo": "1",
        "seoKey": f"{query_slug}-jobs",
        "location": location,
    }
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://www.naukri.com/{query_slug}-jobs",
    }
    response = session.get(
        "https://www.naukri.com/jobapi/v2/search",
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        print(f"Naukri unavailable (status {response.status_code}).")
        return []

    payload = response.json()
    rows = payload.get("list", [])
    jobs = []
    for row in rows:
        link = clean_text(row.get("urlStr"))
        if not link:
            continue

        post = clean_text(row.get("post"))
        title = post.split(" - @ ")[0].strip() if " - @ " in post else post
        if not title:
            title = "Untitled role"

        description = clean_text(strip_html_tags(row.get("jobDesc", "")))
        min_exp = row.get("minExp")
        max_exp = row.get("maxExp")
        if min_exp is not None and max_exp is not None:
            description = f"{description} Experience: {min_exp}-{max_exp} years.".strip()

        jobs.append(
            {
                "source": "Naukri",
                "id": clean_text(row.get("jobId")) or link,
                "title": title,
                "company": clean_text(row.get("companyName")),
                "location": clean_text(row.get("city")) or location,
                "link": link,
                "description": description,
                "skills": clean_text(row.get("keywords")),
            }
        )
        if len(jobs) >= limit:
            break

    return jobs


def scrape_wellfound(session, query, location, limit):
    """
    Best-effort scrape for Wellfound.
    Often blocked by anti-bot checks.
    """
    response = session.get(
        "https://wellfound.com/jobs",
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        print(f"Wellfound unavailable (status {response.status_code}).")
        return []
    return []


def scrape_foundit(session, query, location, limit):
    """Fetches jobs from Foundit middleware JSON endpoint."""
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://www.foundit.in/srp/results?query={query}&locations={location}",
    }
    response = session.get(
        "https://www.foundit.in/middleware/jobsearch",
        params={"query": query, "locations": location, "start": 0, "limit": limit},
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        print(f"Foundit unavailable (status {response.status_code}).")
        return []

    payload = response.json()
    rows = payload.get("jobSearchResponse", {}).get("data", [])
    jobs = []
    for row in rows:
        link = (
            clean_text(row.get("redirectUrl"))
            or clean_text(row.get("jdUrl"))
            or clean_text(row.get("seoJdUrl"))
        )
        if not link:
            continue
        if link.startswith("/"):
            link = urljoin("https://www.foundit.in", link)

        exp = clean_text(row.get("exp"))
        skills = clean_text(row.get("skills"))
        description = skills
        if exp:
            description = f"{description} Experience: {exp}." if description else f"Experience: {exp}."

        jobs.append(
            {
                "source": "Foundit",
                "id": clean_text(row.get("id")) or clean_text(row.get("jobId")) or link,
                "title": clean_text(row.get("title")) or "Untitled role",
                "company": clean_text(row.get("companyName")),
                "location": clean_text(row.get("locations")) or location,
                "link": link,
                "description": description,
                "skills": skills,
            }
        )
        if len(jobs) >= limit:
            break

    return jobs


def extract_cutshort_jobs(html_page):
    """Extracts job list from Cutshort Next.js hydration payload."""
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html_page,
        re.S,
    )
    if not match:
        return []

    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []

    dehydrated_state = payload.get("props", {}).get("pageProps", {}).get("dehydratedState")
    if not isinstance(dehydrated_state, dict):
        return []

    for query in dehydrated_state.get("queries", []):
        state_data = query.get("state", {}).get("data")
        if not isinstance(state_data, dict):
            continue
        page_data = state_data.get("data", {}).get("pageData", {})
        jobs = page_data.get("jobs")
        if isinstance(jobs, list) and jobs:
            return jobs

    return []


def scrape_cutshort(session, query, location, limit):
    """Fetches jobs from Cutshort category pages (best available public path)."""
    query_slug = slugify(query)
    slug_candidates = [f"{query_slug}-jobs", f"{query_slug}-jobs-in-india", "software-engineering-jobs"]
    seen = set()
    ordered_slugs = []
    for slug in slug_candidates:
        if slug and slug not in seen:
            ordered_slugs.append(slug)
            seen.add(slug)

    raw_jobs = []
    for slug in ordered_slugs:
        page_url = f"https://cutshort.io/jobs/{slug}"
        response = session.get(page_url, timeout=REQUEST_TIMEOUT_SECONDS)
        if response.status_code != 200:
            continue
        raw_jobs = extract_cutshort_jobs(response.text)
        if raw_jobs:
            break

    jobs = []
    for row in raw_jobs:
        link = clean_text(row.get("publicUrl"))
        if not link:
            continue

        company = clean_text((row.get("companyDetails") or {}).get("name"))
        if not company:
            company = clean_text((row.get("companyId") or {}).get("name"))

        all_skills = row.get("allSkills") or []
        skills = clean_text(", ".join(str(skill) for skill in all_skills))
        description = clean_text(strip_html_tags(row.get("sanitizedComment", "")))

        jobs.append(
            {
                "source": "Cutshort",
                "id": clean_text(row.get("_id")) or link,
                "title": clean_text(row.get("headline")) or "Untitled role",
                "company": company,
                "location": clean_text(row.get("locationsText") or ", ".join(row.get("locations") or []))
                or location,
                "link": link,
                "description": description,
                "skills": skills,
            }
        )
        if len(jobs) >= limit:
            break

    return jobs


def scrape_instahyre(session, query, location, limit):
    """
    Best-effort scrape for Instahyre.
    Often blocked by anti-bot checks.
    """
    response = session.get(
        "https://www.instahyre.com/search-jobs/?query=" + query,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        print(f"Instahyre unavailable (status {response.status_code}).")
        return []
    return []


def collect_jobs(session, query, location, limit):
    """Collects jobs from all sources and logs per-source counts."""
    sources = [
        ("Indeed", scrape_indeed),
        ("LinkedIn", scrape_linkedin),
        ("Naukri", scrape_naukri),
        ("Wellfound", scrape_wellfound),
        ("Foundit", scrape_foundit),
        ("Cutshort", scrape_cutshort),
        ("Instahyre", scrape_instahyre),
    ]
    all_jobs = []

    for source_name, scraper in sources:
        try:
            source_jobs = scraper(session, query, location, limit)
            print(f"{source_name}: fetched {len(source_jobs)} jobs")
            all_jobs.extend(source_jobs)
        except Exception as exc:
            print(f"{source_name}: scraper error -> {exc}")

    return all_jobs


def scan_jobs():
    """Fetches jobs from supported sources, dedupes, filters and alerts."""
    print(f"Scanning for query='{JOB_QUERY}' and location='{JOB_LOCATION}'...")
    conn = init_db()
    cursor = conn.cursor()
    session = requests.Session()
    session.headers.update(COMMON_HEADERS)

    jobs = collect_jobs(session, JOB_QUERY, JOB_LOCATION, MAX_JOBS_PER_SOURCE)
    print(f"Total fetched jobs: {len(jobs)}")

    sent_count = 0
    seen_in_run = set()
    for job in jobs:
        if not clean_text(job.get("link")):
            continue

        job_key = build_job_key(job)
        if job_key in seen_in_run:
            continue
        seen_in_run.add(job_key)

        cursor.execute("SELECT link FROM seen_jobs WHERE link = ?", (job_key,))
        if cursor.fetchone():
            continue

        matched_skills = find_matched_skills(job)
        if not matched_skills:
            continue

        print(f"Matched {matched_skills} -> {job.get('source')} | {job.get('title')}")
        success = send_telegram_alert(session, job, matched_skills)
        if success:
            cursor.execute("INSERT INTO seen_jobs (link) VALUES (?)", (job_key,))
            conn.commit()
            sent_count += 1
            time.sleep(1)
        else:
            print("Failed to send. Not inserting into DB so it can retry later.")

    conn.close()
    print(f"Scan complete. Alerts sent: {sent_count}")


if __name__ == "__main__":
    scan_jobs()
