# LinkedIn Post Generator

An AI-powered tool that reads content sources, identifies trending topics, and generates LinkedIn posts in your personal tone of voice — powered by Google Gemini.

This project was primarily built using Large Language Models (LLMs).

Currently running live demo at https://giangle.fi/linked-ai.html (Note:I do not collect any data, but you should generally never share API keyss or optionally create a new key for testing and then delete the key right after testing)

## How It Works

```
Sources (RSS / Articles) ──► Fetch Content ──► Pick 5 Trending Topics ──► Generate Posts
                                                                               │
Your Sample Posts ──► Analyze Your Tone ──► Adjust Posts to Match ─────────────┘
                                                                               │
                                                               5 LinkedIn Posts ◄
```

1. **Fetch sources** — Reads URLs (articles + RSS feeds), scrapes content automatically
2. **Pick topics** — Gemini identifies the 5 most trending topics across all sources
3. **Generate posts** — Gemini writes a LinkedIn post for each topic (in Finnish)
4. **Match your tone** — Posts are rewritten to match your writing style from sample posts
5. **Output** — 5 ready-to-publish LinkedIn posts

## Two Ways to Use

### Web App (`web/`)

A browser-based UI where anyone can generate posts using their own Gemini API key. Includes real-time progress, editable sources and samples, and copy-to-clipboard.

```bash
cd web
pip install -r requirements.txt
python app.py
# Open http://localhost:5000
```

See [web/README.md](web/README.md) for full details.

### CLI Script (`local/`)

A command-line Python script for local use. Reads from `sources.md` and `sample.md` files, outputs to `posts-YYYY-MM-DD.md`.

```bash
cd local
pip install -r requirements.txt
set GOOGLE_API_KEY=your-key-here
python generate_posts.py
```

See [local/README.md](local/README.md) for full details.

## Prerequisites

- Python 3.10+
- A [Google Gemini API key](https://aistudio.google.com/apikey)

## Project Structure

```
├── README.md                 # This file
├── local/                    # CLI version
│   ├── generate_posts.py     # Main script
│   ├── sources.md            # Source URLs (one per line)
│   ├── sample.md             # Your sample posts for tone matching
│   ├── requirements.txt      # Python dependencies
│   ├── workflow.md           # CLI workflow documentation
│   └── README.md             # CLI usage guide
└── web/                      # Web UI version
    ├── app.py                # Flask backend
    ├── templates/
    │   └── index.html        # Single-page frontend
    ├── requirements.txt      # Python dependencies
    ├── workflow.md           # Web workflow documentation
    └── README.md             # Web app usage guide
```
