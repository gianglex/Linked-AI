# LinkedIn Post Generator — Web App

A browser-based UI for generating LinkedIn posts with AI. Visitors bring their own Gemini API key (BYOK), edit sources and sample posts, and get 5 ready-to-publish posts with real-time progress updates.

## Quick Start (Development)

```bash
cd web/linked-ai
pip install -r requirements.txt
python linked-ai.py --dev
```

Open `linked-ai.html` directly in your browser, or visit http://localhost:5000/defaults to verify the API is running.

## Features

- **No account needed** — visitors use their own Gemini API key (never stored on the server)
- **Editable sources** — pre-filled with default RSS feeds, fully editable per session
- **Tone matching** — paste your own LinkedIn posts and the AI matches your writing style
- **Real-time progress** — live status updates as the pipeline runs (via Server-Sent Events)
- **Copy to clipboard** — one-click copy for individual posts or all at once
- **Finnish output** — posts are generated in Finnish by default (configurable in `linked-ai.py`)

## How It Works

1. Enter your Gemini API key (get one free at [Google AI Studio](https://aistudio.google.com/apikey))
2. Edit the source URLs (RSS feeds and/or article links)
3. Paste 2-3 of your own LinkedIn posts as tone-of-voice samples
4. Click **Generate 5 LinkedIn Posts**
5. Watch the progress bar, then copy your posts

## Security

This app is designed to be safe for public deployment:

| Protection | Details |
|-----------|---------|
| **BYOK** | API keys are sent per-request and never stored, logged, or written to disk |
| **SSRF prevention** | All user-provided URLs are validated — private IPs, localhost, and metadata endpoints are blocked |
| **Input validation** | Max 20 URLs, 10K chars for sample text, 50KB total payload |
| **Security headers** | CSP, X-Frame-Options, X-Content-Type-Options, XSS protection |
| **No file writes** | Generated posts exist only in the HTTP response |
| **Session storage** | API key is stored in browser `sessionStorage` (auto-cleared on tab close) |

## Configuration

Edit these constants at the top of `linked-ai/linked-ai.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `DEFAULT_MODEL` | `gemini-flash-latest` | Gemini model to use |
| `MAX_ARTICLE_CHARS` | `3000` | Max characters to extract per article |
| `MAX_FEED_ENTRIES` | `10` | Max recent entries per RSS feed |
| `MAX_URLS` | `20` | Max source URLs per request |
| `MAX_SAMPLE_CHARS` | `10000` | Max characters for sample text |

## Architecture

The app is split into two parts for flexible deployment:

- **`linked-ai.html`** — standalone dark-mode frontend, can be placed in any web root
- **`linked-ai/linked-ai.py`** — API-only Flask backend, handles all AI processing

The HTML fetches defaults and sends requests to the backend via relative paths. An `API_BASE` variable at the top of the JS lets you set a prefix if the API is proxied under a sub-path.

## Default Content

The app loads default values from `linked-ai/sources.md` and `linked-ai/sample.md`. The frontend fetches these via the `/defaults` endpoint on page load.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/defaults` | Returns default sources, sample text, and model list as JSON |
| `POST` | `/session` | Creates a session token (API key + inputs in POST body) |
| `POST` | `/models` | Lists available Gemini models (API key in POST body) |
| `GET` | `/generate/<token>` | SSE endpoint — streams progress events and final posts |

## Deployment

The frontend HTML can live in a different directory from the backend (e.g. in your web root):

```
/var/www/html/linked-ai.html              <-- served by Apache/Nginx
/var/www/linkedin-posts/linked-ai/    <-- Flask API (Gunicorn)
```

Apache/Nginx serves the HTML directly and proxies `/defaults`, `/session`, `/models`, `/generate/` to Flask.

See **DEPLOYMENT.md** for full step-by-step instructions including Apache, Nginx, systemd, and SSL setup.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Please provide a valid Gemini API key" | Enter your key in the field at the top of the page |
| "Model not found" | The model may not be available on your plan — try `gemini-flash-latest` |
| "Rate limited" | The Gemini free tier may have been exceeded — wait a few minutes |
| "Blocked URL" | The URL points to a private/internal address (SSRF protection) |
| Posts not appearing | Check the progress panel for errors; ensure your API key is valid |
