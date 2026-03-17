import csv
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from io import StringIO

from flask import Flask, Response, jsonify, render_template, request

DB_PATH = os.environ.get("JOBS_DB_PATH", "jobs.db")
DEFAULT_DAYS_FILTER = os.environ.get("DASHBOARD_DEFAULT_DAYS", "30")

SOURCE_ORDER = [
    "Foundit",
    "LinkedIn",
    "Naukri",
    "Cutshort",
    "Indeed",
    "Wellfound",
    "Instahyre",
    "Other",
]

app = Flask(__name__)


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_dashboard_db():
    """Ensures dashboard table exists even before first successful alert."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sent_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_key TEXT UNIQUE NOT NULL,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            company TEXT,
            location TEXT,
            link TEXT NOT NULL,
            matched_skills TEXT,
            all_skills TEXT,
            description TEXT,
            min_experience REAL,
            max_experience REAL,
            sent_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def safe_int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def normalize_days_filter(value):
    if value is None or value == "":
        value = DEFAULT_DAYS_FILTER

    value = str(value).strip().lower()
    if value == "all":
        return "all", None

    parsed = safe_int(value, 30)
    if parsed < 1:
        parsed = 1
    if parsed > 3650:
        parsed = 3650
    return str(parsed), parsed


def parse_sent_at(sent_at):
    if not sent_at:
        return None
    try:
        normalized = sent_at.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def format_sent_at(sent_at):
    dt = parse_sent_at(sent_at)
    if dt is None:
        return sent_at or "-"
    local_dt = dt.astimezone()
    return local_dt.strftime("%d %b %Y, %I:%M %p")


def fetch_jobs(search_text, days_limit, source_filter):
    conn = get_connection()

    where_clauses = []
    params = []

    if search_text:
        like = f"%{search_text.lower()}%"
        where_clauses.append(
            """
            (
                lower(title) LIKE ?
                OR lower(company) LIKE ?
                OR lower(location) LIKE ?
                OR lower(matched_skills) LIKE ?
                OR lower(description) LIKE ?
            )
            """
        )
        params.extend([like, like, like, like, like])

    if days_limit is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_limit)
        where_clauses.append("sent_at >= ?")
        params.append(cutoff.isoformat(timespec="seconds"))

    if source_filter and source_filter.lower() != "all":
        where_clauses.append("source = ?")
        params.append(source_filter)

    query = """
        SELECT
            id,
            job_key,
            source,
            title,
            company,
            location,
            link,
            matched_skills,
            all_skills,
            description,
            min_experience,
            max_experience,
            sent_at
        FROM sent_alerts
    """
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)
    query += " ORDER BY sent_at DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    jobs = []
    for row in rows:
        row_dict = dict(row)
        row_dict["source"] = (row_dict.get("source") or "Other").strip() or "Other"
        row_dict["company"] = row_dict.get("company") or "Unknown company"
        row_dict["location"] = row_dict.get("location") or "Unknown location"
        row_dict["sent_at_display"] = format_sent_at(row_dict.get("sent_at"))
        row_dict["matched_skills_list"] = [
            skill.strip()
            for skill in (row_dict.get("matched_skills") or "").split(",")
            if skill.strip()
        ]
        jobs.append(row_dict)
    return jobs


def group_jobs_by_source(jobs):
    grouped = {source: [] for source in SOURCE_ORDER}
    for job in jobs:
        source = job.get("source", "Other")
        if source not in grouped:
            source = "Other"
        grouped[source].append(job)
    return grouped


def compute_stats(jobs):
    today_utc = datetime.now(timezone.utc).date()
    today_count = 0
    unique_companies = set()
    active_sources = set()

    for job in jobs:
        company = (job.get("company") or "").strip()
        if company:
            unique_companies.add(company.lower())

        source = (job.get("source") or "").strip()
        if source:
            active_sources.add(source)

        dt = parse_sent_at(job.get("sent_at"))
        if dt and dt.astimezone(timezone.utc).date() == today_utc:
            today_count += 1

    return {
        "total_jobs": len(jobs),
        "today_jobs": today_count,
        "companies": len(unique_companies),
        "active_sources": len(active_sources),
    }


@app.route("/")
def dashboard():
    init_dashboard_db()

    search_text = (request.args.get("q") or "").strip()
    source_filter = (request.args.get("source") or "all").strip()
    days_filter_raw, days_limit = normalize_days_filter(request.args.get("days"))

    jobs = fetch_jobs(search_text=search_text, days_limit=days_limit, source_filter=source_filter)
    grouped = group_jobs_by_source(jobs)
    stats = compute_stats(jobs)

    return render_template(
        "dashboard.html",
        jobs_by_source=grouped,
        source_order=SOURCE_ORDER,
        stats=stats,
        filters={
            "q": search_text,
            "source": source_filter,
            "days": days_filter_raw,
        },
    )


@app.route("/api/jobs")
def jobs_api():
    init_dashboard_db()

    search_text = (request.args.get("q") or "").strip()
    source_filter = (request.args.get("source") or "all").strip()
    _, days_limit = normalize_days_filter(request.args.get("days"))

    jobs = fetch_jobs(search_text=search_text, days_limit=days_limit, source_filter=source_filter)
    return jsonify({"count": len(jobs), "jobs": jobs})


@app.route("/export.csv")
def export_csv():
    init_dashboard_db()

    search_text = (request.args.get("q") or "").strip()
    source_filter = (request.args.get("source") or "all").strip()
    _, days_limit = normalize_days_filter(request.args.get("days"))
    jobs = fetch_jobs(search_text=search_text, days_limit=days_limit, source_filter=source_filter)

    stream = StringIO()
    writer = csv.writer(stream)
    writer.writerow(
        [
            "source",
            "title",
            "company",
            "location",
            "matched_skills",
            "sent_at",
            "link",
        ]
    )
    for job in jobs:
        writer.writerow(
            [
                job.get("source"),
                job.get("title"),
                job.get("company"),
                job.get("location"),
                ", ".join(job.get("matched_skills_list", [])),
                job.get("sent_at"),
                job.get("link"),
            ]
        )

    filename_date = datetime.now().strftime("%Y%m%d_%H%M")
    response = Response(stream.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=job_alerts_{filename_date}.csv"
    return response


if __name__ == "__main__":
    init_dashboard_db()
    app.run(host="127.0.0.1", port=5000, debug=False)
