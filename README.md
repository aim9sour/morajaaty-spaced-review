# Morajaaty

**A local-first spaced repetition platform for serious self-study, Arabic RTL workflows, flexible flashcard imports, and private learning analytics.**

Morajaaty is a lightweight review system built with FastAPI, SQLite, and vanilla JavaScript. It helps learners organize material into categories, import flashcards or programming concepts, review with a practical spaced repetition engine, and optionally ask an AI companion questions about their own local study data.

The project is designed to stay useful without becoming heavy. It runs locally, stores your study history in SQLite, avoids mandatory accounts or hosted databases, and keeps the frontend dependency-light.

## Table of Contents

- [Why Morajaaty](#why-morajaaty)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [How Reviews Work](#how-reviews-work)
- [Import Formats](#import-formats)
- [AI Study Companion](#ai-study-companion)
- [Privacy Model](#privacy-model)
- [Project Structure](#project-structure)
- [Development](#development)
- [Contributing](#contributing)
- [Roadmap](#roadmap)
- [License](#license)

## Why Morajaaty

Many spaced repetition tools are powerful, but they can also be account-bound, cloud-first, or hard to adapt to personal study workflows. Morajaaty focuses on a smaller promise:

- keep the data local and understandable
- make daily review friction low
- support Arabic RTL use cases properly
- allow flexible imports instead of forcing one rigid card shape
- expose useful analytics without turning the app into a dashboard maze
- let an optional AI companion inspect local study progress through read-only tools

## Features

- **Local-first storage**: cards, review history, settings, providers, and API keys are stored in local SQLite.
- **Two-level organization**: create main categories and subcategories, then review a whole main category or a specific subcategory.
- **Date-only scheduling**: cards are due by calendar day, not by exact hour.
- **Custom spaced repetition engine**: `easy`, `hard`, and `wrong` move cards between learning and review stages.
- **Load-balanced long intervals**: longer schedules are placed near the target date while avoiding overloaded review days.
- **Multiple variants per card**: attach several question/answer wordings to one card. Review sessions rotate variants without splitting scheduling, counters, or accuracy.
- **Programming concepts mode**: import one-face concepts into special concept categories with a separate progression model.
- **Flexible JSON and text imports**: support `question`/`answer`, `front`/`back`, wrapped objects, card variants, JSON concept lists, and bracketed text concepts.
- **Session summaries**: distinguish first-time graduations, re-graduations, removed cards, and remaining due items.
- **AI study companion**: optional Gemini or OpenAI-compatible provider support with streaming responses and read-only database tools.
- **Provider management**: configure OpenAI-compatible base URLs, custom headers/query params, model fetching, and per-session model selection.
- **Dependency-light UI**: vanilla HTML/CSS/JavaScript with an Arabic RTL interface.
- **Permissive open source license**: MIT License for broad use, modification, redistribution, and commercial use.

## Tech Stack

- **Backend**: FastAPI
- **Database**: SQLite
- **Frontend**: Vanilla HTML, CSS, JavaScript
- **AI integrations**: Google GenAI SDK and official OpenAI SDK for compatible providers
- **Streaming**: Server-Sent Events
- **Runtime**: Python 3.11+ recommended

## Quick Start

### Requirements

- Python 3.11 or newer
- A modern browser
- Node.js only if you want to run the JavaScript syntax check

The AI companion is optional. You can use the review platform without adding any AI key.

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

## Configuration

Morajaaty creates `data.sqlite3` locally on first run. No external database is required.

From the settings page you can configure:

- display/user name used by the companion context
- Gemini API keys
- OpenAI-compatible providers
- provider base URL and API key
- optional organization/project fields
- optional default headers and query parameters
- default model

API keys are stored in the local SQLite database. Do not commit `data.sqlite3` or `.env` files; both are intentionally ignored by Git.

## How Reviews Work

Morajaaty organizes learning content into main categories and subcategories. Normal flashcards are imported into subcategories and reviewed in focused sessions.

Each normal card starts in the **learning** stage. Repeated `easy` answers graduate it into the **review** stage. `hard` and `wrong` reset or increase the required effort so weak cards receive more attention instead of being pushed too far into the future.

Normal card intervals progress through:

```text
1 day -> 3 days -> 7 days -> 15 days -> 32 days -> 90 days
```

For longer intervals, the scheduler checks nearby dates and prefers a day with a lighter workload. This keeps future review sessions more balanced.

### Card Variants

Normal cards can contain several variants: different question/answer wordings for the same underlying knowledge.

Variants are not separate review items. The parent card keeps one schedule, one accuracy history, and one set of counters. During review, Morajaaty selects one variant and records which variant appeared. Future appearances avoid recently shown variants where possible, so the learner is less likely to memorize the visual pattern or ordering of a card.

### Programming Concepts Mode

A main category can be marked as a programming concepts root. Subcategories inside it accept one-face concept cards instead of normal question/answer cards.

Concept intervals progress through:

```text
1 day -> 3 days -> 7 days -> 15 days -> 30 days -> 90 days
```

Before a concept enters long-term review, `hard` adds 2 daily repetitions and `wrong` adds 4 daily repetitions. During long-term review, `hard` drops the concept back by two interval levels and adds 2 daily repetitions; `wrong` resets it back to daily learning.

Concept review sessions allow moving next/previous and jumping directly to an unanswered concept. Once a concept is rated, it leaves the current session.

## Import Formats

Normal flashcard subcategories accept JSON. Concept subcategories accept JSON or plain text.

### Basic Flashcards

Use `question` and `answer`:

```json
[
  {
    "question": "What is spaced repetition?",
    "answer": "A learning technique that reviews information at increasing intervals.",
    "notes": "Optional notes can be attached to each card."
  }
]
```

Or use `front` and `back`:

```json
[
  {
    "front": "Question",
    "back": "Answer",
    "notes": "Optional notes"
  }
]
```

Wrapped objects are also supported:

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

### Multi-Variant Flashcards

Add a `variants` list to create several wordings for the same card. If the parent object also has `front`/`back` or `question`/`answer`, those parent fields become the first variant automatically.

```json
[
  {
    "front": "Question wording A",
    "back": "Answer wording A",
    "notes": "Optional shared notes",
    "variants": [
      {
        "front": "Question wording B",
        "back": "Answer wording B"
      },
      {
        "front": "Question wording C",
        "back": "Answer wording C",
        "notes": "Optional notes for this wording"
      }
    ]
  }
]
```

You can also import a variants-only card:

```json
[
  {
    "variants": [
      {
        "front": "Question wording A",
        "back": "Answer wording A"
      },
      {
        "front": "Question wording B",
        "back": "Answer wording B"
      }
    ]
  }
]
```

Ready-to-use examples:

- [examples/cards.sample.json](examples/cards.sample.json)
- [examples/cards.variants.sample.json](examples/cards.variants.sample.json)

### Programming Concepts

Concept categories accept JSON strings:

```json
[
  "Binary search",
  "Event loop",
  "Dependency injection"
]
```

They also accept plain text, one concept per line:

```text
Binary search
Event loop
Dependency injection
```

Bracketed text works too:

```text
[Binary search]
[Event loop]
[Dependency injection]
```

Concept objects can include notes:

```json
[
  {
    "concept": "Database transaction",
    "notes": "ACID is a useful anchor when reviewing this concept."
  }
]
```

See [examples/concepts.sample.txt](examples/concepts.sample.txt) for a plain-text concept import sample.

## AI Study Companion

The built-in companion can answer questions about your actual local study data when enabled. It can use read-only tools for:

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

During chat, the interface streams status updates, thinking traces, tool calls, and final answers. If one configured key fails, the companion reports the attempt and falls back to another available key when possible.

Without an AI key or provider, the review platform still works locally.

## Privacy Model

Morajaaty is intentionally local-first:

- `data.sqlite3` is created locally and ignored by Git.
- `.venv` is ignored by Git.
- `.env` files are ignored by Git.
- API keys are stored in the local SQLite database managed by the app.
- The AI companion only sends chat context and tool results when you explicitly use it.
- Database query tools available to the companion are read-only.

## Project Structure

```text
app/
  main.py        # FastAPI routes, review logic, AI companion streaming
  database.py    # SQLite connection, schema creation, migrations
static/
  index.html     # App shell
  app.js         # Frontend state, routing, review UI, settings, providers, chat
  styles.css     # Arabic RTL interface styling
examples/
  cards.sample.json
  cards.variants.sample.json
  concepts.sample.txt
requirements.txt
```

## Development

### Install for Development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Run Locally

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Validation

```powershell
.\.venv\Scripts\python.exe -m compileall app
node --check static\app.js
```

Linux/macOS equivalents:

```bash
python -m compileall app
node --check static/app.js
```

## Contributing

Contributions are welcome. Good contributions for this project are usually small, focused, and easy to review.

Recommended workflow:

1. Fork the repository.
2. Create a feature branch from `main`.
3. Keep the change scoped to one feature, bug fix, or documentation improvement.
4. Run the validation commands before opening a pull request.
5. Explain what changed, why it changed, and how you tested it.

Useful contribution areas:

- import/export workflows
- scheduling tests
- accessibility improvements for the RTL interface
- analytics and review summaries
- packaging for easier desktop use
- documentation and examples

Please avoid committing local data files, virtual environments, API keys, or generated secrets.

## Roadmap

- Export and backup flows for cards and review history.
- More scheduling tests around edge cases and variant rotation.
- Richer long-term learning analytics.
- Optional encryption for stored API keys.
- Desktop packaging for non-developer installation.
- More accessible keyboard-first review workflows.

## License

Morajaaty is open source under the [MIT License](LICENSE).

The MIT License is a permissive license: people can use, copy, modify, merge, publish, distribute, sublicense, and sell copies of the software, as long as the license notice is included. The software is provided without warranty.
