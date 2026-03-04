"""
LinkedIn Post Generation Workflow
=================================
Reads source links from sources.md, identifies trending topics via Gemini,
generates LinkedIn posts, and adjusts tone based on sample.md.

Usage:
    set GOOGLE_API_KEY=your-api-key-here
    python generate_posts.py
"""

import os
import re
import sys
import time
from datetime import date
from urllib.parse import urlparse

import feedparser
import requests
import urllib3
from bs4 import BeautifulSoup

# Suppress SSL verification warnings (needed on networks with proxy/firewall)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SOURCES_FILE = "sources.md"
SAMPLE_FILE = "sample.md"
MODEL = "gemini-3-pro-preview"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_ARTICLE_CHARS = 3000  # Trim scraped article text to keep prompts manageable
MAX_FEED_ENTRIES = 10     # Max recent entries to pull per RSS feed


# ---------------------------------------------------------------------------
# Gemini API helper (direct REST – avoids SDK SSL issues)
# ---------------------------------------------------------------------------
def gemini_generate(api_key: str, prompt: str, max_tokens: int = 4000) -> str:
    """Call the Gemini REST API directly and return the generated text.
    Retries automatically on rate-limit (429) errors."""
    url = f"{GEMINI_API_URL}/{MODEL}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }

    max_retries = 5
    for attempt in range(max_retries):
        resp = requests.post(
            url,
            json=payload,
            timeout=120,
            verify=False,
        )
        if resp.status_code == 429:
            wait = 2 ** attempt * 15  # 15s, 30s, 60s, 120s, 240s
            print(f"  Rate limited. Waiting {wait}s before retry ({attempt + 1}/{max_retries})...")
            time.sleep(wait)
            continue
        if resp.status_code == 404:
            print(f"ERROR: Model '{MODEL}' not found. It may not be available on your plan.")
            print(f"  Run: python generate_posts.py --list-models")
            print(f"  to see which models you have access to.")
            sys.exit(1)
        if not resp.ok:
            print(f"ERROR: Gemini API returned {resp.status_code}: {resp.text}")
            sys.exit(1)
        break
    else:
        print("ERROR: Still rate-limited after multiple retries. Try again later.")
        sys.exit(1)

    data = resp.json()

    # Extract text from response
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        print("ERROR: Unexpected Gemini API response:")
        print(data)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Stage 1 – Read sources.md
# ---------------------------------------------------------------------------
def read_sources(path: str) -> list[str]:
    """Return a list of URLs from the sources file (skip blanks & comments)."""
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    urls: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        urls.append(stripped)
    return urls


# ---------------------------------------------------------------------------
# Stage 2 – Fetch & extract content
# ---------------------------------------------------------------------------
def _is_rss_url(url: str) -> bool:
    """Heuristic check whether a URL is likely an RSS/Atom feed."""
    path = urlparse(url).path.lower()
    rss_patterns = ["/rss", "/feed", "/atom", ".xml", ".rss"]
    if any(p in path for p in rss_patterns):
        return True
    if "feedburner" in url.lower():
        return True
    return False


def _fetch_article(url: str) -> dict | None:
    """Scrape an article page and return title + truncated body text."""
    try:
        resp = requests.get(url, timeout=15, verify=False, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0.0.0 Safari/537.36"
        })
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  [WARN] Could not fetch article {url}: {exc}")
        return None

    # If the response is actually XML/RSS, handle it as a feed instead
    content_type = resp.headers.get("Content-Type", "")
    if "xml" in content_type or "rss" in content_type or "atom" in content_type:
        return None  # Caller will retry as feed

    soup = BeautifulSoup(resp.text, "lxml")

    title = ""
    if soup.title:
        title = soup.title.get_text(strip=True)

    # Remove script/style tags
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    body_text = soup.get_text(separator="\n", strip=True)
    body_text = re.sub(r"\n{3,}", "\n\n", body_text)  # Collapse blank lines
    body_text = body_text[:MAX_ARTICLE_CHARS]

    return {"type": "article", "url": url, "title": title, "content": body_text}


def _fetch_feed(url: str) -> list[dict]:
    """Parse an RSS/Atom feed and return a list of entry summaries."""
    try:
        feed = feedparser.parse(url)
    except Exception as exc:
        print(f"  [WARN] Could not parse feed {url}: {exc}")
        return []

    entries: list[dict] = []
    for entry in feed.entries[:MAX_FEED_ENTRIES]:
        title = entry.get("title", "(no title)")
        summary = entry.get("summary", entry.get("description", ""))
        # Strip HTML from summary
        if summary:
            summary = BeautifulSoup(summary, "lxml").get_text(strip=True)
        link = entry.get("link", url)
        entries.append({
            "type": "feed_entry",
            "url": link,
            "title": title,
            "content": summary[:1500] if summary else "",
        })
    return entries


def fetch_all_sources(urls: list[str]) -> list[dict]:
    """Fetch content from every URL, auto-detecting article vs RSS feed."""
    sources: list[dict] = []
    for url in urls:
        print(f"Fetching: {url}")
        if _is_rss_url(url):
            entries = _fetch_feed(url)
            if entries:
                sources.extend(entries)
                print(f"  -> Got {len(entries)} feed entries")
            else:
                # Fallback: try as article
                article = _fetch_article(url)
                if article:
                    sources.append(article)
                    print(f"  -> Scraped as article (feed parse failed)")
        else:
            article = _fetch_article(url)
            if article:
                sources.append(article)
                print(f"  -> Scraped article: {article['title'][:60]}")
            else:
                # Fallback: try as feed
                entries = _fetch_feed(url)
                if entries:
                    sources.extend(entries)
                    print(f"  -> Parsed as feed (article scrape failed)")
                else:
                    print(f"  -> SKIPPED (could not fetch)")
    return sources


# ---------------------------------------------------------------------------
# Stage 3 – Pick 5 trending topics (Gemini)
# ---------------------------------------------------------------------------
def pick_topics(api_key: str, sources: list[dict]) -> str:
    """Ask Gemini to identify the 5 most popular / trending topics."""
    source_block = "\n\n---\n\n".join(
        f"**{s['title']}**\nURL: {s['url']}\n{s['content']}"
        for s in sources
    )

    prompt = f"""You are a social-media trend analyst. Below are summaries of recent articles and feed entries from various sources.

Identify the **5 most currently popular and trending topics** that would resonate well on LinkedIn. For each topic provide:
1. A short topic title (in Finnish)
2. A 2-3 sentence description of why it is trending (in Finnish)
3. One or two source URLs that back it up

Write ALL output in Finnish. Return ONLY a numbered list (1-5) with the above structure. No extra commentary.

--- BEGIN SOURCES ---
{source_block}
--- END SOURCES ---"""

    return gemini_generate(api_key, prompt, max_tokens=2000)


# ---------------------------------------------------------------------------
# Stage 4 – Generate LinkedIn posts & adjust tone
# ---------------------------------------------------------------------------
def generate_posts(api_key: str, topics: str) -> str:
    """Generate a LinkedIn post draft for each of the 5 topics."""
    prompt = f"""You are an expert LinkedIn content creator who writes in Finnish.

Below are 5 trending topics. You MUST write exactly 5 LinkedIn posts — one for EACH topic. Do NOT skip any topic.

Each post should be IN FINNISH (150-250 words) and should:
- Start with a strong hook (first line grabs attention)
- Use short paragraphs and line breaks for readability
- Include a call-to-action or question at the end
- Add 3-5 relevant hashtags at the bottom (hashtags can be in English or Finnish)

Write the ENTIRE post in Finnish. Separate each post with a line containing only "---".

IMPORTANT: You must produce exactly 5 complete posts separated by "---". Do not stop after one post.

Topics:
{topics}"""

    return gemini_generate(api_key, prompt, max_tokens=8000)


def adjust_tone(api_key: str, drafts: str, sample: str) -> str:
    """Refine posts to match the tone of voice from sample.md."""
    prompt = f"""You are a writing style expert who works in Finnish. Below are two sections:

1. **SAMPLE POSTS** – These are example LinkedIn posts written by the user in Finnish. Analyze the tone, voice, formatting style, sentence structure, emoji usage (or lack thereof), humor, and overall vibe.

2. **DRAFT POSTS** – These are AI-generated LinkedIn post drafts in Finnish.

Your task: Rewrite each draft post IN FINNISH so it matches the tone, voice, and style of the sample posts **as closely as possible**, while keeping the core topic and information intact.

You MUST rewrite ALL 5 draft posts. Do NOT skip any. Keep each post between 150-250 words. Keep the language Finnish throughout. Separate posts with "---". Do NOT add any commentary – output ONLY the 5 rewritten posts.

--- SAMPLE POSTS ---
{sample}
--- END SAMPLE POSTS ---

--- DRAFT POSTS ---
{drafts}
--- END DRAFT POSTS ---"""

    return gemini_generate(api_key, prompt, max_tokens=8000)


def read_sample(path: str) -> str:
    """Read the sample.md file for tone-of-voice reference."""
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Stage 5 – Write output
# ---------------------------------------------------------------------------
def write_output(posts: str) -> str:
    """Write posts to posts-<currentdate>.md and return the filename."""
    today = date.today().strftime("%Y-%m-%d")
    filename = f"posts-{today}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"# LinkedIn Posts – {today}\n\n")
        f.write(posts)
        f.write("\n")
    return filename


# ---------------------------------------------------------------------------
# List available models
# ---------------------------------------------------------------------------
def list_models(api_key: str):
    """Fetch and print all available Gemini models for this API key."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    resp = requests.get(url, timeout=30, verify=False)
    if not resp.ok:
        print(f"ERROR: Could not list models ({resp.status_code}): {resp.text}")
        sys.exit(1)

    data = resp.json()
    models = data.get("models", [])

    print(f"\nAvailable Gemini models ({len(models)}):\n")
    print(f"  {'Model ID':<40} {'Display Name'}")
    print(f"  {'-'*40} {'-'*30}")
    for m in models:
        model_id = m.get("name", "").replace("models/", "")
        display = m.get("displayName", "")
        # Only show generateContent-capable models
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" in methods:
            print(f"  {model_id:<40} {display}")

    print(f"\nTo use a model, set MODEL in generate_posts.py to one of the IDs above.\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # Check API key
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("ERROR: Set the GOOGLE_API_KEY environment variable first.")
        print("  Example:  set GOOGLE_API_KEY=your-api-key-here")
        sys.exit(1)

    # Handle --list-models flag
    if "--list-models" in sys.argv:
        list_models(api_key)
        return

    # Stage 1 – Read sources
    print("\n=== Stage 1: Reading sources ===")
    urls = read_sources(SOURCES_FILE)
    if not urls:
        print(f"ERROR: No URLs found in {SOURCES_FILE}. Add one URL per line.")
        sys.exit(1)
    print(f"Found {len(urls)} source URL(s).\n")

    # Stage 2 – Fetch content
    print("=== Stage 2: Fetching content ===")
    sources = fetch_all_sources(urls)
    if not sources:
        print("ERROR: Could not fetch content from any source.")
        sys.exit(1)
    print(f"\nCollected {len(sources)} content item(s).\n")

    # Stage 3 – Pick trending topics
    print("=== Stage 3: Identifying 5 trending topics ===")
    topics = pick_topics(api_key, sources)
    print(topics)
    print()

    # Stage 4 – Generate & tone-match posts
    print("=== Stage 4: Generating LinkedIn posts ===")
    drafts = generate_posts(api_key, topics)
    print("Drafts generated. Adjusting tone of voice...\n")

    sample = read_sample(SAMPLE_FILE)
    final_posts = adjust_tone(api_key, drafts, sample)
    print("Tone adjustment complete.\n")

    # Stage 5 – Write output
    print("=== Stage 5: Writing output ===")
    filename = write_output(final_posts)
    print(f"Done! Posts saved to: {filename}\n")


if __name__ == "__main__":
    main()
