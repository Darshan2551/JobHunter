import requests
import feedparser
import sqlite3
import time
import os

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
    message = f"🚀 <b>New Job Match!</b>\n\n<b>Role:</b> {job_title}\n<b>Link:</b> {job_link}"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    
    response = requests.post(url, json=payload)
    return response.status_code == 200

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
            
        # 2. Check if it matches your stack
        if any(skill in title for skill in TARGET_SKILLS):
            success = send_telegram_alert(entry.title, link)
            
            # 3. If sent successfully, save to DB so we don't send it again
            if success:
                print(f"Alert sent: {entry.title}")
                cursor.execute('INSERT INTO seen_jobs (link) VALUES (?)', (link,))
                conn.commit()
                time.sleep(1) 

    conn.close()
    print("Scan complete.")

if __name__ == '__main__':
    scan_jobs()
