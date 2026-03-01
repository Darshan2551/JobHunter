import requests
import feedparser
import sqlite3
import time
import os
import html
import re

# Pull secrets from environment variables (crucial for GitHub Actions)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

RSS_URL = 'https://weworkremotely.com/categories/remote-programming-jobs.rss'
TARGET_SKILLS = [
    'python', 'javascript', 'react', 'node.js', 'express', 'mysql', 'mongodb', 
    'php', 'opencv', 'tensorflow', 'mediapipe', 'langchain', 'full-stack', 
    'backend', 'frontend', 'ai', 'computer vision'
]

def init_db():
    """Creates the database and table if they don't exist."""
    conn = sqlite3.connect('jobs.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS seen_jobs (
            link TEXT PRIMARY KEY
        )
    ''')
    conn.commit()
    return conn

def send_telegram_alert(job_title, job_link):
    """Pushes the formatted message to your Telegram chat."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    # Escape HTML special characters to prevent Telegram API errors
    safe_title = html.escape(job_title)
    
    message = f"🚀 <b>New Job Match!</b>\n\n<b>Role:</b> {safe_title}\n<b>Link:</b> {job_link}"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            print(f"Failed to send alert: {response.status_code} - {response.text}")
        return response.status_code == 200
    except Exception as e:
        print(f"Exception sending alert: {e}")
        return False

def scan_jobs():
    """Fetches the RSS feed, checks the DB, and filters for skills."""
    print("Scanning for new roles...")
    feed = feedparser.parse(RSS_URL)
    conn = init_db()
    cursor = conn.cursor()
    
    for entry in feed.entries:
        title = entry.title.lower()
        link = entry.link
        
        # 1. Check if we've already seen this job
        cursor.execute('SELECT link FROM seen_jobs WHERE link = ?', (link,))
        if cursor.fetchone():
            continue # Skip to the next job if it's already in the DB
            
        # 2. Check if it matches your stack (using regex for word boundaries)
        # We replace some symbols in title or use word boundaries so 'ai' doesn't match 'email'
        matched_skills = [skill for skill in TARGET_SKILLS if re.search(rf'\b{re.escape(skill)}\b', title)]
        if matched_skills:
            print(f"Matched skills: {matched_skills} for job: {entry.title}")
            success = send_telegram_alert(entry.title, link)
            
            # 3. If sent successfully, save to DB so we don't send it again
            if success:
                print(f"Alert sent: {entry.title}")
                cursor.execute('INSERT INTO seen_jobs (link) VALUES (?)', (link,))
                conn.commit()
                time.sleep(1)
            else:
                print("Failed to send, not inserting into DB to retry later.") 

    conn.close()
    print("Scan complete.")

if __name__ == '__main__':
    scan_jobs()
