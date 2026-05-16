# Morajaaty

**A local-first spaced repetition platform with date-based scheduling, flexible imports, learning analytics, and an AI study companion.**

Morajaaty is a lightweight study system built with FastAPI, SQLite, and a vanilla HTML/CSS/JavaScript frontend. It is designed for learners who want full control over their study data, a fast local workflow, and a practical review algorithm that adapts to how well each card is remembered.

The project runs entirely on your machine by default. Your cards, review history, settings, provider configuration, and API keys are stored locally in SQLite. The AI companion is optional and can be enabled by adding your own Gemini key or an OpenAI-compatible provider from the settings page.

## Highlights

- **Local-first by design**: no hosted backend, no mandatory account, no remote database.
- **Date-only review scheduling**: due cards are based on the calendar day, not the exact hour a previous review happened.
- **Custom spaced repetition engine**: cards move between learning and review stages based on `easy`, `hard`, and `wrong` answers.
- **Load-balanced scheduling**: longer intervals are placed near the target date while avoiding overloaded review days, without hour offsets.
- **Programming concepts mode**: mark a main category as a concept root to import and review one-face concepts with a separate daily algorithm.
- **Flexible imports**: bring normal flashcards as JSON, or concepts as JSON strings, text lines, or bracketed items like `[concept]`.
- **AI study companion**: ask questions about your progress, due workload, difficult cards, and review history.
- **Gemini and OpenAI-compatible providers**: configure Gemini keys or OpenAI-compatible base URLs, fetch models, and choose defaults from settings.
- **Read-only analytics tools for the agent**: the companion can inspect local statistics and run safe SQLite read queries.
- **Fast, dependency-light frontend**: no heavy UI framework; just HTML, CSS, and JavaScript.
- **Arabic RTL interface**: built for Arabic study workflows while keeping the codebase straightforward and portable.

## Tech Stack

- **Backend**: FastAPI
- **Database**: SQLite
- **Frontend**: Vanilla HTML, CSS, JavaScript
- **AI integration**: Google GenAI SDK and official OpenAI SDK for compatible providers
- **Transport**: Server-Sent Events for live assistant output

## Quick Start

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## How It Works

Morajaaty organizes learning content into main categories and subcategories. Normal cards are imported into subcategories as flashcards, then reviewed through a focused study session.

Each card starts in the **learning** stage. Repeated `easy` answers graduate the card into the **review** stage. `hard` and `wrong` answers reset or increase the required effort so weak cards get more attention instead of being pushed too far into the future.

Review due dates are stored and evaluated as calendar dates. If you review a card today, it can become due tomorrow regardless of the hour you studied.

Review intervals progress through:

```text
1 day -> 3 days -> 7 days -> 15 days -> 32 days -> 90 days
```

For longer intervals, the scheduler checks nearby dates and prefers a day with a lighter workload. This keeps review sessions more balanced over time.

Review summaries separate cards that graduated for the first time from cards that had already graduated before and returned after a mistake.

### Programming Concepts Mode

A main category can be marked as a programming concepts root. Subcategories inside that root accept one-face concept cards instead of normal question/answer cards.

Concepts use a separate progression:

```text
1 day -> 3 days -> 7 days -> 15 days -> 30 days -> 90 days
```

Before a concept enters long-term review, `hard` adds 2 daily repetitions and `wrong` adds 4 daily repetitions. During long-term review, `hard` drops the concept back by two interval levels and adds 2 daily repetitions; `wrong` resets the concept back to daily learning.

Concept review sessions allow moving next/previous and jumping directly to an unanswered concept. Once a concept is rated, it leaves the current session.

## AI Study Companion

The built-in companion is designed to be useful rather than decorative. When enabled, it can answer questions about your actual local study data using read-only tools:

- platform statistics
- database schema inspection
- safe SQLite `SELECT`, `WITH`, and `PRAGMA` queries

Example questions:

- Which cards are causing the most mistakes?
- How many cards are due today?
- Which category needs the most attention?
- Did my accuracy improve over the last week?
- What should I review first?

The companion can use Gemini keys or OpenAI-compatible providers. Provider settings include base URL, API key, organization, project, optional headers, optional query parameters, timeout, and retry settings.

From settings, each OpenAI-compatible provider can fetch its available models. Re-fetching updates existing model metadata and adds new models without duplicating the same model id.

During chat, the interface streams status updates, thinking traces, tool calls, and final answers. If one configured key fails, the companion reports the attempt and falls back to another available key when possible.

Without an AI key or provider, the rest of the application still works locally.

## Import Format

Normal flashcard subcategories accept a plain JSON array:

```json
[
  {
    "question": "What is spaced repetition?",
    "answer": "A learning technique that reviews information at increasing intervals.",
    "notes": "Optional notes can be attached to each card."
  }
]
```

It also supports `front` and `back` fields:

```json
[
  {
    "front": "Question",
    "back": "Answer",
    "notes": "Optional notes"
  }
]
```

Or a wrapped object:

```json
{
  "cards": [
    {
      "question": "Question",
      "answer": "Answer"
    }
  ]
}
```

See [examples/cards.sample.json](examples/cards.sample.json) for a ready-to-use sample.

Concept subcategories accept JSON strings:

```json
[
  "Binary search",
  "Event loop",
  "Dependency injection"
]
```

They also accept plain text, one concept per line, including bracketed items:

```text
[انا بطل]
[تيستاوي]
```

## Privacy Model

Morajaaty is intentionally simple and local:

- `data.sqlite3` is created locally and ignored by Git.
- `.venv` is ignored by Git.
- `.env` files are ignored by Git.
- API keys are stored in the local SQLite database managed by the app.
- The AI companion only sends chat context and tool results when you explicitly use it.

## Project Structure

```text
app/
  main.py        # FastAPI routes, review logic, AI companion streaming
  database.py    # SQLite connection, schema creation, migrations
static/
  index.html     # App shell
  app.js         # Frontend state, routing, review UI, settings, providers, chat
  styles.css     # RTL interface styling
examples/
  cards.sample.json
requirements.txt
```

## Development Check

```powershell
.\.venv\Scripts\python.exe -m compileall app
node --check static\app.js
```

Or:

```bash
python -m compileall app
node --check static/app.js
```

## Roadmap Ideas

- Export review history and cards.
- Add optional encryption for stored API keys.
- Add richer dashboards for long-term learning trends.
- Add tests around scheduling behavior and import validation.
- Package the app for simpler desktop installation.

## License

Morajaaty is open source under the [MIT License](LICENSE).
