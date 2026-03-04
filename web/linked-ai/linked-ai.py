"""
LinkedIn Post Generator – Web UI
=================================
A Flask web app that lets visitors generate LinkedIn posts using their own
Gemini API key (BYOK). Includes SSRF protection, input validation, and
SSE progress streaming.

Security:
    - API keys are never in URLs — session token flow keeps them out of logs
    - SSL verification enabled in production (disable with --dev for proxied envs)
    - SSRF protection blocks private/internal IP ranges

Usage:
    Production:  python linked-ai.py
    Development: python linked-ai.py --dev
"""

import ipaddress
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
from datetime import date
from urllib.parse import urljoin, urlparse

import feedparser
import requests
import urllib3
from bs4 import BeautifulSoup
from flask import Flask, Response, jsonify, request, stream_with_context

# ---------------------------------------------------------------------------
# Mode detection (before anything else)
# ---------------------------------------------------------------------------
DEV_MODE = "--dev" in sys.argv or os.environ.get("FLASK_ENV") == "development"

if DEV_MODE:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# SSL verification: enabled in production, disabled in dev (for corporate proxies)
SSL_VERIFY = not DEV_MODE

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100_000  # 100KB max request payload

# Redis for session token storage (shared across Gunicorn workers)
_DEFAULT_REDIS = "redis://localhost:6379" if not DEV_MODE else ""
REDIS_URL = os.environ.get("REDIS_URL", _DEFAULT_REDIS)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "gemini-flash-latest"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_ARTICLE_CHARS = 3000
MAX_FEED_ENTRIES = 10
MAX_URLS = 20
MAX_SAMPLE_CHARS = 10_000

# Popular models shown by default in the dropdown
POPULAR_MODELS = [
    "gemini-flash-latest",
    "gemini-2.5-flash-preview-05-20",
    "gemini-2.5-pro-preview-05-06",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]

# Session token settings
SESSION_TTL = 300  # 5 minutes max lifetime for a session token

# Load defaults from the web/ folder's own files
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_default_file(filename: str) -> str:
    """Load a default file from the web/ folder."""
    path = os.path.join(SCRIPT_DIR, filename)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return ""


DEFAULT_SOURCES = _load_default_file("sources.md")
DEFAULT_SAMPLE = _load_default_file("sample.md")

# ---------------------------------------------------------------------------
# Session token store (Redis in production, in-memory fallback for dev)
# ---------------------------------------------------------------------------
_redis_client = None
if REDIS_URL.startswith("redis"):
    try:
        import redis as _redis_mod
        _redis_client = _redis_mod.Redis.from_url(REDIS_URL, decode_responses=True)
        _redis_client.ping()
        print("Session store: Redis")
    except Exception:
        _redis_client = None
        print("Session store: in-memory (Redis unavailable)")
else:
    print("Session store: in-memory")

# In-memory fallback
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()

_SESSION_PREFIX = "linkedai:session:"


def _create_session(api_key: str, sources: str, sample: str, model: str) -> str:
    """Create a short-lived session and return its token."""
    token = secrets.token_urlsafe(32)
    session_data = {
        "api_key": api_key,
        "sources": sources,
        "sample": sample,
        "model": model,
        "created_at": str(time.time()),
    }

    if _redis_client:
        key = f"{_SESSION_PREFIX}{token}"
        _redis_client.hset(key, mapping=session_data)
        _redis_client.expire(key, SESSION_TTL)
    else:
        with _sessions_lock:
            session_data["created_at"] = time.time()
            _sessions[token] = session_data
    return token


def _consume_session(token: str) -> dict | None:
    """Retrieve and delete a session (single-use). Returns None if expired/missing."""
    if _redis_client:
        key = f"{_SESSION_PREFIX}{token}"
        pipe = _redis_client.pipeline()
        pipe.hgetall(key)
        pipe.delete(key)
        results = pipe.execute()
        session = results[0]
        if not session:
            return None
        # Convert created_at back to float
        session["created_at"] = float(session.get("created_at", 0))
        return session
    else:
        with _sessions_lock:
            session = _sessions.pop(token, None)
        if session is None:
            return None
        if time.time() - session["created_at"] > SESSION_TTL:
            return None
        return session


def _cleanup_expired_sessions():
    """Remove expired in-memory sessions (Redis handles expiry automatically)."""
    while True:
        time.sleep(60)
        if _redis_client:
            continue  # Redis TTL handles cleanup
        now = time.time()
        with _sessions_lock:
            expired = [k for k, v in _sessions.items() if now - v["created_at"] > SESSION_TTL]
            for k in expired:
                del _sessions[k]


# Start cleanup thread
_cleanup_thread = threading.Thread(target=_cleanup_expired_sessions, daemon=True)
_cleanup_thread.start()


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # No CSP here — the HTML is served by Apache/Nginx which should set CSP.
    # API responses are JSON/SSE and don't need CSP.
    return response


# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------
BLOCKED_IP_RANGES = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def is_safe_url(url: str) -> tuple[bool, str]:
    """Check if a URL is safe to fetch (no SSRF)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL format"

    if parsed.scheme not in ("http", "https"):
        return False, f"Blocked scheme: {parsed.scheme}"

    hostname = parsed.hostname
    if not hostname:
        return False, "No hostname in URL"

    if hostname.lower() in ("localhost", "0.0.0.0"):
        return False, "Blocked hostname"

    # Resolve hostname and check IP
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in infos:
            ip = ipaddress.ip_address(sockaddr[0])
            for blocked in BLOCKED_IP_RANGES:
                if ip in blocked:
                    return False, f"Blocked IP range for {hostname}"
    except socket.gaierror:
        return False, f"Cannot resolve hostname: {hostname}"

    return True, ""


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------
def parse_sources_text(text: str) -> list[str]:
    """Parse source URLs from text, skip blanks and comments."""
    urls = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        urls.append(stripped)
    return urls


def validate_inputs(api_key: str, sources_text: str, sample_text: str, model: str) -> tuple[bool, str, list[str]]:
    """Validate all inputs. Returns (ok, error_message, urls)."""
    if not api_key or len(api_key) < 10:
        return False, "Please provide a valid Gemini API key.", []

    if not model or not re.match(r"^[a-zA-Z0-9._-]+$", model):
        return False, "Invalid model name.", []

    if not sources_text.strip():
        return False, "Sources cannot be empty.", []

    if len(sample_text) > MAX_SAMPLE_CHARS:
        return False, f"Sample text too long (max {MAX_SAMPLE_CHARS} characters).", []

    urls = parse_sources_text(sources_text)
    if not urls:
        return False, "No valid URLs found in sources.", []

    if len(urls) > MAX_URLS:
        return False, f"Too many URLs (max {MAX_URLS}).", []

    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"Invalid URL scheme: {url}", []

    return True, "", urls


# ---------------------------------------------------------------------------
# Content fetching (with SSRF protection)
# ---------------------------------------------------------------------------
def _is_rss_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    rss_patterns = ["/rss", "/feed", "/atom", ".xml", ".rss"]
    if any(p in path for p in rss_patterns):
        return True
    if "feedburner" in url.lower():
        return True
    return False


def _safe_get(url: str, **kwargs) -> requests.Response:
    """GET with redirect-aware SSRF protection. Re-checks each hop."""
    kwargs.setdefault("timeout", 15)
    kwargs.setdefault("verify", SSL_VERIFY)
    kwargs["allow_redirects"] = False
    kwargs.setdefault("headers", {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/125.0.0.0 Safari/537.36"
    })

    max_redirects = 5
    for _ in range(max_redirects):
        resp = requests.get(url, **kwargs)
        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get("Location", "")
            if not location:
                raise requests.RequestException("Redirect with no Location header")
            # Resolve relative redirects
            url = urljoin(url, location)
            safe, reason = is_safe_url(url)
            if not safe:
                raise requests.RequestException(f"Redirect blocked: {reason}")
            continue
        return resp
    raise requests.RequestException("Too many redirects")


def _fetch_article(url: str) -> dict | None:
    safe, reason = is_safe_url(url)
    if not safe:
        return None

    try:
        resp = _safe_get(url)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    content_type = resp.headers.get("Content-Type", "")
    if "xml" in content_type or "rss" in content_type or "atom" in content_type:
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    body_text = soup.get_text(separator="\n", strip=True)
    body_text = re.sub(r"\n{3,}", "\n\n", body_text)[:MAX_ARTICLE_CHARS]

    return {"type": "article", "url": url, "title": title, "content": body_text}


def _fetch_feed(url: str) -> list[dict]:
    safe, reason = is_safe_url(url)
    if not safe:
        return []

    # Fetch the feed content ourselves (with SSRF-safe redirects)
    # then parse the raw XML, so feedparser never makes its own HTTP calls.
    try:
        resp = _safe_get(url)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception:
        return []

    entries = []
    for entry in feed.entries[:MAX_FEED_ENTRIES]:
        title = entry.get("title", "(no title)")
        summary = entry.get("summary", entry.get("description", ""))
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


def fetch_all_sources(urls: list[str], progress_cb=None) -> list[dict]:
    sources = []
    for i, url in enumerate(urls):
        if progress_cb:
            progress_cb(f"Fetching ({i+1}/{len(urls)}): {url[:80]}")

        if _is_rss_url(url):
            entries = _fetch_feed(url)
            if entries:
                sources.extend(entries)
            else:
                article = _fetch_article(url)
                if article:
                    sources.append(article)
        else:
            article = _fetch_article(url)
            if article:
                sources.append(article)
            else:
                entries = _fetch_feed(url)
                if entries:
                    sources.extend(entries)
    return sources


# ---------------------------------------------------------------------------
# Gemini API (direct REST)
# ---------------------------------------------------------------------------
def gemini_generate(api_key: str, prompt: str, max_tokens: int = 4000, model: str = DEFAULT_MODEL) -> str:
    url = f"{GEMINI_API_URL}/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }

    max_retries = 3
    for attempt in range(max_retries):
        resp = requests.post(url, json=payload, timeout=120, verify=SSL_VERIFY)
        if resp.status_code == 429:
            wait = 2 ** attempt * 15
            time.sleep(wait)
            continue
        if resp.status_code == 404:
            raise ValueError(f"Model '{model}' not found. It may not be available on your plan.")
        if not resp.ok:
            raise ValueError(f"Gemini API error {resp.status_code}: {resp.text[:200]}")
        break
    else:
        raise ValueError("Rate limited by Gemini API. Please try again later.")

    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise ValueError(f"Unexpected Gemini response: {json.dumps(data)[:300]}")


def pick_topics(api_key: str, sources: list[dict], model: str = DEFAULT_MODEL) -> str:
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
    return gemini_generate(api_key, prompt, max_tokens=2000, model=model)


def generate_posts(api_key: str, topics: str, model: str = DEFAULT_MODEL) -> str:
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
    return gemini_generate(api_key, prompt, max_tokens=8000, model=model)


def adjust_tone(api_key: str, drafts: str, sample: str, model: str = DEFAULT_MODEL) -> str:
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
    return gemini_generate(api_key, prompt, max_tokens=8000, model=model)


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------
def sse_event(event: str, data: str) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def sse_error(message: str) -> str:
    return sse_event("error", message)


def sse_progress(message: str) -> str:
    return sse_event("progress", message)


def sse_result(posts: str) -> str:
    return sse_event("result", posts)


def sse_done() -> str:
    return sse_event("done", "complete")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/defaults")
def defaults():
    """Return default configuration for the frontend (sources, sample, models)."""
    return jsonify({
        "sources": DEFAULT_SOURCES,
        "sample": DEFAULT_SAMPLE,
        "default_model": DEFAULT_MODEL,
        "popular_models": POPULAR_MODELS,
    })


@app.route("/session", methods=["POST"])
def create_session():
    """Create a short-lived session token. API key stays in POST body, never in URLs."""
    data = request.get_json(silent=True) or {}
    api_key = data.get("api_key", "").strip()
    sources_text = data.get("sources", "").strip()
    sample_text = data.get("sample", "").strip()
    model = data.get("model", DEFAULT_MODEL).strip()

    # Validate everything before creating a session
    ok, error, urls = validate_inputs(api_key, sources_text, sample_text, model)
    if not ok:
        return jsonify({"error": error}), 400

    # SSRF check all URLs upfront
    for url in urls:
        safe, reason = is_safe_url(url)
        if not safe:
            return jsonify({"error": f"Blocked URL: {url} — {reason}"}), 400

    token = _create_session(api_key, sources_text, sample_text, model)
    return jsonify({"token": token})


@app.route("/models", methods=["POST"])
def list_models():
    """Fetch available generative models from the Gemini API. Key sent via POST body."""
    data = request.get_json(silent=True) or {}
    api_key = data.get("api_key", "").strip()
    if not api_key or len(api_key) < 10:
        return jsonify({"error": "Please provide a valid API key."}), 400

    try:
        resp = requests.get(
            f"{GEMINI_API_URL}?key={api_key}",
            timeout=15,
            verify=SSL_VERIFY,
        )
        if not resp.ok:
            return jsonify({"error": f"Gemini API error {resp.status_code}"}), resp.status_code
        data = resp.json()
    except requests.RequestException as exc:
        return jsonify({"error": str(exc)}), 502

    models = []
    for m in data.get("models", []):
        name = m.get("name", "")
        short = name.replace("models/", "") if name.startswith("models/") else name
        methods = m.get("supportedGenerationMethods", [])
        if "generateContent" in methods:
            models.append({
                "id": short,
                "displayName": m.get("displayName", short),
            })

    return jsonify({"models": models})


@app.route("/generate/<token>")
def generate(token):
    """SSE endpoint — streams progress and results. Token-based, no secrets in URL."""
    session = _consume_session(token)

    def stream():
        if session is None:
            yield sse_error("Session expired or invalid. Please try again.")
            return

        api_key = session["api_key"]
        sources_text = session["sources"]
        sample_text = session["sample"]
        model = session["model"]

        urls = parse_sources_text(sources_text)

        # Stage 1: Fetch content
        yield sse_progress(f"Fetching content from {len(urls)} source(s)...")

        try:
            sources = fetch_all_sources(urls)
        except Exception as exc:
            yield sse_error(f"Error fetching sources: {str(exc)}")
            return

        if not sources:
            yield sse_error("Could not fetch content from any source.")
            return

        yield sse_progress(f"Collected {len(sources)} content item(s).")

        # Stage 2: Pick topics
        yield sse_progress(f"Identifying 5 trending topics (model: {model})...")
        try:
            topics = pick_topics(api_key, sources, model=model)
        except ValueError as exc:
            yield sse_error(str(exc))
            return

        yield sse_progress("Topics identified. Generating posts...")

        # Stage 3: Generate posts
        try:
            drafts = generate_posts(api_key, topics, model=model)
        except ValueError as exc:
            yield sse_error(str(exc))
            return

        yield sse_progress("Drafts generated. Adjusting tone of voice...")

        # Stage 4: Tone adjustment
        try:
            final_posts = adjust_tone(api_key, drafts, sample_text, model=model)
        except ValueError as exc:
            yield sse_error(str(exc))
            return

        yield sse_progress("Done!")
        yield sse_result(final_posts)
        yield sse_done()

    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    if DEV_MODE:
        print("Starting in DEVELOPMENT mode (SSL verify OFF)...")
        print(f"Open http://localhost:{port} in your browser.\n")
        app.run(debug=True, host="0.0.0.0", port=port)
    else:
        try:
            from gunicorn.app.wsgiapp import run as gunicorn_run

            print(f"Starting production server on port {port} (SSL verify ON)...")
            sys.argv = [
                "gunicorn",
                "wsgi:application",
                "--bind", f"127.0.0.1:{port}",
                "--workers", os.environ.get("WORKERS", "2"),
                "--timeout", "300",
                "--access-logfile", "-",
            ]
            gunicorn_run()
        except ImportError:
            print("Gunicorn not found. Install it with: pip install gunicorn")
            print("Or run in dev mode: python linked-ai.py --dev")
            sys.exit(1)
