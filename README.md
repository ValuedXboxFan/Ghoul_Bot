# Media Club

A Discord bot and web application for Movie of the Week, media reviews, activity
tracking, recaps, and future community games.

## Version history

The original Ghoul Bot is preserved on the `legacy/v1` branch and at the
`v1-final` tag. Version 2 development begins on `staging`; the production
branch remains `master`.

## Delivery pipeline

The repository uses two long-lived branches:

- `staging` automatically deploys to the Railway staging environment.
- `master` automatically deploys to the Railway production environment.

Normal work follows this path:

1. Create a short-lived branch from `staging`, such as `feature/movie-search`.
2. Open a pull request into `staging`.
3. GitHub Actions runs linting, formatting checks, type checks, and tests.
4. Merge after the checks pass. Railway deploys `staging` automatically.
5. Test the feature using the staging bot, staging Discord server, and staging web URL.
6. When a collection of changes is ready, open a pull request from `staging` into `master`.
7. Merge that promotion pull request after approval. Railway deploys production.

Production should never receive untested feature branches directly.

## Environment isolation

Staging and production must each have their own:

- Discord application and bot token
- Discord server or isolated test channels
- PostgreSQL database
- OAuth client secret and callback URL
- Railway variables and public domain

External media API keys can be shared initially if their terms permit it, but separate keys
make usage and failures easier to diagnose.

## Local setup

Requires Python 3.12.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn media_club.app:app --reload
```

Run the same quality checks used by GitHub:

```powershell
ruff check .
ruff format --check .
mypy
pytest
```

The health endpoint is available at `http://127.0.0.1:8000/health`.

The readiness endpoint at `http://127.0.0.1:8000/ready` reports PostgreSQL and
Discord connectivity. It returns HTTP 503 until both dependencies are connected.

## Discord commands

The staging bot registers commands directly to `DISCORD_GUILD_ID`, so they appear
immediately without waiting for global command propagation:

- `/ping` confirms the Gateway connection and displays latency; it requires **Manage Server**.
- `/status` reports the active environment and PostgreSQL connectivity; it requires
  **Manage Server**.
- `/log` adds media to the member's private ledger. `/post` follows the same workflow, adds
  the same ledger entry, and also creates a forum thread. Both require an inline media type
  and title.
- Movies offer **Search TMDB** or **Enter manually**; the supplied title is searched directly
  or pre-populated for manual entry. A matched movie shows its first five available TMDB
  posters together for selection. Other types open manual entry with the supplied title.
- After either lookup or manual entry, a **Media setup complete** checkpoint explicitly
  separates the shared title/artwork from the member's review, rating, and date. The member
  can add those log details or cancel the unfinished entry.
- Completed action prompts are replaced as the logging flow advances. A prompt that opens a
  modal is dismissed immediately, so closing that modal requires restarting `/log` or `/post`.
- Every ledger entry can include an optional review, strict 1–5 half-step rating, and optional
  `Month/Day/Year` activity date. New entries default to the current Chicago activity day,
  which changes at 4 AM.
- `/edit` and `/delete` operate only inside a registered forum post and only for its
  registered owner. Editing media details updates the existing thread title, tag, artwork,
  and starter message in place, preserving the thread's discussion and links.
- `/mylogs` privately browses and edits both forum-posted and private history, newest first,
  with pagination beyond 25 entries.
- `/about` shows Ghoul Bot's credits and data-source attribution.

Set `DISCORD_MEDIA_FORUM_CHANNEL_ID` to the target forum channel ID. The legacy
`DISCORD_MOVIE_FORUM_CHANNEL_ID` variable also remains supported. Create these forum
tags exactly as written: `Movies`, `TV Shows`, `Books`, `Comics`, `Podcasts`, `Music`,
`Video Games`, and `Other`. The bot needs permission to send messages, attach files,
embed links, and manage threads in that forum.

Database migrations run automatically before the web service starts.

## Media data model

Provider-backed media is normalized separately from member activity:

- `media_items` stores one shared item for each `(provider, provider_id)` pair, including the
  canonical title and TMDB ID.
- `media_logs` stores each member's individual date, rating, review, selected poster, and forum
  post identifiers, referencing the shared item through `media_item_id`.

This lets multiple members log the same TMDB movie without duplicating its identity, while
preserving independent watches and presentation choices. The indexed relationship supports
future queries for everyone who watched a particular movie and links back to their forum posts.

Movie and television metadata is supplied by TMDB. This product uses the TMDB API
but is not endorsed or certified by TMDB.

## Railway layout

Use one Railway project with two environments:

- `staging`, connected to the GitHub `staging` branch
- `production`, connected to the GitHub `master` branch

Each environment should contain an application service and its own PostgreSQL service.
Configure Railway to wait for the GitHub Actions workflow before deployment if that option
is available for the connected repository.

