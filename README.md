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

## Railway layout

Use one Railway project with two environments:

- `staging`, connected to the GitHub `staging` branch
- `production`, connected to the GitHub `master` branch

Each environment should contain an application service and its own PostgreSQL service.
Configure Railway to wait for the GitHub Actions workflow before deployment if that option
is available for the connected repository.

