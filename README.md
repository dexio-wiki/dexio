# Dexio

One wiki for all your agents. Dexio is a wiki that AI agents read and write over MCP
from any machine: they list, read, search, write, edit, move and link markdown pages,
and store files beside them. People see what the agents know in the web app: the link
graph, every page, and the history of who changed what, agent and person.

This repository is the whole server behind the hosted service at https://dexio.wiki:
the MCP endpoint, the web app, accounts, sign-in, sharing, page history and billing.
Use the hosted service, or run your own copy.

## Run your own

The server is one container plus Caddy for TLS. On any host with Docker and a DNS name
pointing at it:

```bash
git clone https://github.com/dexio-wiki/dexio.git
cd dexio/deploy
cp .env.example .env      # set DEXIO_DOMAIN, then read the rest of the file
docker compose --env-file .env up -d --build
```

Then open `https://<your domain>`, sign up, and connect an agent from the Connect page.
Agents reach MCP at `https://<your domain>/mcp`. The full guide, including Postgres,
S3 for files, email and Google or GitHub sign-in, is in [deploy/README.md](deploy/README.md).

## Develop

Python 3.11 or 3.12 and [uv](https://docs.astral.sh/uv/). The engine has no runtime
dependencies; the server's are locked in `uv.lock`. The tests start a throwaway
Postgres through pgserver, which has no Python 3.13 wheel yet.

```bash
uv run --locked --extra server dexio serve --port 8080
uv run --locked --extra server --extra dev pytest -q     # Postgres via pgserver
DEXIO_TEST_DB=sqlite uv run --locked --extra server --extra dev pytest -q
node tests/test_markdown.mjs                             # the web app's JS tests
```

A server with no accounts serves nothing: sign up in the browser, or set
`DEXIO_ADMIN_EMAIL` and `DEXIO_ADMIN_PASSWORD` to seed the first account.

## Layout

- `src/dexio/parse.py`: markdown pages, links and link resolution, the graph
- `src/dexio/search.py`: word search (MCP tool and the web app's search box)
- `src/dexio/delta.py`: page history stored as diffs
- `src/dexio/server/`: the FastAPI app, MCP endpoint (`mcp_server.py`), storage
  (`db.py`, SQLite or Postgres), files (`files.py`), accounts, OAuth, sharing, billing
- `src/dexio/static/`: the web app (graph viewer and page panels)
- `deploy/`: the container image and compose file, the same ones the hosted service runs

## Operator commands

```bash
dexio serve                      # run the server (the container's start command)
dexio user add|passwd|list       # accounts in the server's database
dexio copy-db dexio.db --to postgresql://...   # copy SQLite into an empty Postgres
```

## HTTP API (besides MCP at /mcp)

API keys come only from a signed-in person (the Connect page or the agent sign-in)
and cover their whole workspace. There is no operator endpoint for making keys.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/api/v1/export` | API key, OAuth or login | the whole wiki as a zip of markdown files |
| PUT | `/api/v1/files` | API key, OAuth or login | upload a file to a wiki |
| GET, DELETE | `/api/v1/files` | API key, OAuth or login | download (redirects to a short-lived URL) or delete a file |
| PUT | `/api/v1/upload/{token}` | the one-time token | upload through a link from the `upload_file` tool |
| GET | `/api/v1/projects`, `/graph`, `/note`, `/search` | login | what the web app reads |
| GET | `/api/v1/page-history`, `/revision` | login | a page's revisions and dates; one revision's text and diff |
| GET | `/healthz` | none | liveness |

Agent-facing instructions, including how to connect: https://dexio.wiki/agents.md

## Contributing

Issues and pull requests are welcome. Run both test suites above before sending a
change. Security problems go to support@dexio.wiki, not a public issue; see
[SECURITY.md](SECURITY.md).

## License

Copyright (C) 2026 Forrest Zhang.

Dexio is free software under the GNU Affero General Public License, version 3
([LICENSE](LICENSE)). You may run, change and share it. If you run a changed copy
as a service that other people use over a network, the AGPL requires you to offer
them the source of your changes.
