# LinkedIn Post Generator

An AI-powered workflow that reads content sources, identifies trending topics, and generates LinkedIn posts in your personal tone of voice — all powered by Google Gemini.

## How It Works

```
sources.md ──► Fetch Content ──► Pick 5 Trending Topics ──► Generate Posts
                                                                  │
sample.md ──► Analyze Your Tone ──► Adjust Posts to Match ────────┘
                                                                  │
                                              posts-YYYY-MM-DD.md ◄
```

1. **Read sources** — Parses URLs from `sources.md` (one per line). Supports both article links and RSS/Atom feeds.
2. **Fetch content** — Scrapes articles with BeautifulSoup or parses RSS feeds with feedparser. Auto-detects the type.
3. **Pick topics** — Sends all collected content to Gemini and asks it to identify the 5 most trending topics.
4. **Generate posts** — Gemini writes a LinkedIn post for each topic, then rewrites them to match the tone of voice found in `sample.md`.
5. **Save output** — Final posts are written to `posts-<today's date>.md`.

## Prerequisites

- Python 3.10+
- A [Google AI API key](https://aistudio.google.com/apikey)

## Setup

1. **Clone the repo** and navigate into the project folder:

   ```bash
   cd "LI test"
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Set your API key:**

   Windows (PowerShell):
   ```powershell
   $env:GOOGLE_API_KEY = "your-api-key-here"
   ```

   Windows (CMD):
   ```cmd
   set GOOGLE_API_KEY=your-api-key-here
   ```

   macOS / Linux:
   ```bash
   export GOOGLE_API_KEY="your-api-key-here"
   ```

## Configuration

### sources.md

Add your content sources — one URL per line. Lines starting with `#` are treated as comments and ignored. Both regular article URLs and RSS/Atom feed URLs are supported.

```markdown
# Tech news
https://feeds.feedburner.com/TechCrunch/
https://news.ycombinator.com/rss
https://www.theverge.com/rss/index.xml

# Individual articles
https://example.com/some-interesting-article
```

### sample.md

Paste 2–3 of your own LinkedIn posts here. The AI analyzes your writing style — sentence structure, formatting, tone, emoji usage, hashtag patterns — and rewrites the generated posts to match. The more representative examples you provide, the better the tone matching will be.

## Usage

Run the script:

```bash
python generate_posts.py
```

The script will print progress through each stage and save the final posts to a file named `posts-YYYY-MM-DD.md` (e.g., `posts-2026-03-03.md`).

## Output

The output file contains 5 LinkedIn-ready posts separated by `---` dividers. Each post includes:

- A strong opening hook
- Short, scannable paragraphs
- A call-to-action or question
- Relevant hashtags

## Customization

You can tweak these constants at the top of `generate_posts.py`:

| Constant | Default | Description |
|----------|---------|-------------|
| `MODEL` | `gemini-2.0-flash` | Gemini model to use |
| `MAX_ARTICLE_CHARS` | `3000` | Max characters to extract per article |
| `MAX_FEED_ENTRIES` | `10` | Max recent entries to pull per RSS feed |

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ERROR: Set the GOOGLE_API_KEY` | Make sure the environment variable is set in your current terminal session |
| `[WARN] Could not fetch article` | The site may be blocking scrapers or the URL may be invalid — check the link |
| `No URLs found in sources.md` | Add at least one URL to `sources.md` (lines starting with `#` are ignored) |
| Empty or short posts | Add more/better sources, or increase `MAX_ARTICLE_CHARS` |
