# Contributing

## Prerequisites

- **Python 3.11+** — [python.org/downloads](https://www.python.org/downloads/) or `brew install python@3.11`
- **Node.js 20.19+** — [nodejs.org](https://nodejs.org/) or `brew install node`
- **Docker** — [docker.com/get-docker](https://www.docker.com/products/docker-desktop/) (for PostgreSQL)
- **Ollama** — [ollama.com](https://ollama.com) or `brew install ollama` (for the AI agent)

## Local Setup

### 1. Clone the repo

Clone this repository:

```bash
git clone <your-repo-url>
cd brownsville-adult-pediatric-clinic
```

### 2. Bootstrap dependencies

The fastest path is to let the Makefile create the backend virtualenv and install both backend and frontend dependencies:

```bash
make backend-setup
make frontend-setup
```

This creates `backend/.venv`, installs the backend package with dev dependencies, and installs the frontend packages.

If you prefer to do the backend setup manually, run:

```bash
cd backend
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -e ".[dev]"
cd ..
```

Backend-oriented `make` targets automatically use `backend/.venv/bin/python` when it exists, so you do not need to keep the virtualenv activated for commands like `make migrate`, `make test`, or `make dev-backend`.

### 3. Create your backend env file

```bash
cp backend/.env.example backend/.env
```

The default settings point to a local Ollama server with the `llama3.2` model. No API key is needed.

### 4. Set up Ollama

Start the Ollama server and pull the model used by the AI agent:

```bash
ollama serve   # skip if using the Ollama desktop app
ollama pull llama3.2
```

### 5. Start PostgreSQL

```bash
make db-up
```

This starts a PostgreSQL 16 container on port 5432 (user: `bapc`, password: `bapc`, database: `bapc`).

### 6. Run the full setup flow

If you already ran `make backend-setup`, `make frontend-setup`, and `make db-up`, you can continue with migrations and seed data directly:

```bash
make migrate
make seed
```

This creates the database tables and populates them with sample patients, appointments, and development login accounts (see the README for the credentials).

Or run the full bootstrap from the project root in one command:

```bash
make setup
```

`make setup` runs backend setup, frontend install, starts PostgreSQL, applies migrations, seeds the database, and generates frontend API types.

### 7. Start developing

Open **two terminal windows**:

```bash
# Terminal 1 — backend (from project root)
make dev-backend
# Runs on http://localhost:8000

# Terminal 2 — frontend (from project root)
make dev-frontend
# Runs on http://localhost:3000
```

Open http://localhost:3000 in your browser and sign in. Admins land on the dashboard; patients land on the chat.

### 8. Verify everything works

- **Dashboard** (http://localhost:3000) — shows patient and scheduled appointment counts
- **Patients page** (http://localhost:3000/patients) — lists seeded patients
- **Appointments page** (http://localhost:3000/appointments) — lists and schedules appointments
- **Chat** (http://localhost:3000/chat) — as admin try "Schedule a vaccination for Ethan Nguyen tomorrow at 2pm", then click Confirm (requires Ollama running)
- **API docs** (http://localhost:8000/docs) — interactive Swagger UI
- **Tests**: `make test` (requires PostgreSQL running; uses a separate `bapc_test` database that is created automatically)

---

## Project Structure

```
brownsville-adult-pediatric-clinic/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, route registration
│   │   ├── models.py          # SQLAlchemy models (Patient, Appointment)
│   │   ├── schemas.py         # Pydantic request/response schemas
│   │   ├── database.py        # Database engine and session setup
│   │   ├── config.py          # Settings from environment variables
│   │   ├── seed.py            # Sample data seeder
│   │   ├── auth.py            # Session cookies + role dependencies
│   │   ├── security.py        # Password hashing (scrypt), session tokens
│   │   ├── create_admin.py    # CLI: make create-admin
│   │   ├── services/
│   │   │   └── scheduling.py  # All scheduling rules (shared by REST, chat, MCP)
│   │   ├── routes/            # API route handlers
│   │   │   ├── auth.py        # login, logout, me, register
│   │   │   ├── patients.py
│   │   │   ├── appointments.py
│   │   │   └── chat.py        # SSE streaming chat endpoint
│   │   ├── agent/
│   │   │   ├── agent.py       # ReAct agent (LangGraph)
│   │   │   ├── tools.py       # Role-scoped tools (propose writes, never write directly)
│   │   │   └── pending.py     # Server-side confirmation gate for chat writes
│   │   └── mcp/
│   │       └── server.py      # MCP server (resources + tools)
│   ├── tests/
│   │   ├── conftest.py        # Pytest fixtures
│   │   ├── test_routes.py     # Patient route tests
│   │   └── test_appointments.py
│   ├── alembic/               # Database migrations
│   └── pyproject.toml         # Python dependencies
├── web/
│   ├── app/                   # Next.js App Router pages
│   │   ├── page.tsx           # Dashboard
│   │   ├── patients/          # Patients list + detail
│   │   ├── appointments/    # Appointments list + scheduling
│   │   └── chat/page.tsx      # Chat interface
│   ├── components/            # React components
│   ├── lib/api.ts             # API client helpers
│   └── package.json
├── docker-compose.yml         # PostgreSQL
└── Makefile                   # All project commands
```

---

## Conventions

### Backend

- **Models** go in `backend/app/models.py` — one file keeps it simple for this project size
- **Schemas** (Pydantic) go in `backend/app/schemas.py` — use `*Create` for input, `*Read` for output
- **Routes** go in `backend/app/routes/` — one file per resource, prefixed with `/api/`
- **Agent tools** go in `backend/app/agent/tools.py` — add to the `all_tools` list
- **MCP resources/tools** go in `backend/app/mcp/server.py`
- Use `async/await` for all database operations
- Follow the existing patterns — look at how `patients.py` and `appointments.py` are structured

### Database

- Create migrations with `make migration msg="description"`
- Apply with `make migrate`
- Seed data uses fixed UUIDs so it's idempotent — you can run `make seed` multiple times safely
- The test suite runs against a separate `<db>_test` database with per-test rollback, so it never touches development data
- All appointment writes (REST, chat, MCP) go through `backend/app/services/scheduling.py`; add new rules there
- `make seed` never creates tables (run `make migrate` first) and only inserts missing rows

### Frontend

- **Pages** use the Next.js App Router in `web/app/`
- **Components** go in `web/components/`
- The frontend proxies `/api/*` requests to the backend (see `next.config.js`)
- Use the `api` helper from `web/lib/api.ts` for data fetching
- Tailwind CSS for styling, shadcn/ui patterns for components

### Schema Pipeline

After changing any Pydantic schemas in `backend/app/schemas.py`:

```bash
make generate-types
```

This exports the OpenAPI spec and generates TypeScript types in `web/generated/api/`.

### Quality Gates

Before committing, run:

```bash
make quality-gates
```

This runs: format (ruff) -> lint (ruff) -> test (pytest).

### Security Audits

To check dependency advisories locally, run:

```bash
make audit
```

This runs `pip-audit` against the backend environment and `npm audit` against the frontend lockfile.

### Testing

- `make test` runs the backend test suite through the project Python environment and does not require `source backend/.venv/bin/activate`
- `make format`, `make lint`, `make migrate`, `make seed`, `make dev-backend`, and `make mcp-server` use the same backend interpreter selection
- If `backend/.venv` is missing, run `make backend-setup`

### Git

- Make small, logical commits with clear messages
- Don't squash

---

## Common Tasks

### Adding a new model

1. Add the SQLAlchemy model to `backend/app/models.py`
2. Add Pydantic schemas to `backend/app/schemas.py`
3. Import the model in `backend/alembic/env.py` (so Alembic sees it)
4. `make migration msg="add your_model"`
5. `make migrate`
6. Add routes in `backend/app/routes/your_model.py`
7. Register the router in `backend/app/main.py`
8. Add seed data to `backend/app/seed.py` if helpful

### Adding an agent tool

1. Add an async function with `@tool` decorator in `backend/app/agent/tools.py`
2. Write a descriptive docstring — the agent reads it to decide when to use the tool
3. Add the function to the `all_tools` list
4. Restart the backend

### Adding an MCP resource

1. Add a new entry to `list_resources()` in `backend/app/mcp/server.py`
2. Handle it in `read_resource()`
3. Restart the MCP server

### Adding an MCP tool

1. Add a new entry to `list_tools()` in `backend/app/mcp/server.py`
2. Handle it in `call_tool()`
3. Restart the MCP server

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `make db-up` fails | Make sure Docker is running |
| `make backend-setup` fails | Make sure `python3` is on your PATH and supports `venv` |
| `make migrate` fails with connection error | Run `make db-up` first and wait a few seconds |
| `make test` fails with `ModuleNotFoundError` | Run `make backend-setup` to install the backend dependencies into `backend/.venv` |
| Chat returns errors | Make sure Ollama is running (`ollama serve`) and the model is pulled (`ollama pull llama3.2`) |
| Every page redirects to /login | Sign in; run `make seed` to create the development accounts |
| Port 5432 already in use by another Postgres container | Start this one on another port: `BAPC_DB_PORT=5433 make db-up`, and set `BAPC_DATABASE_URL` to port 5433 in `backend/.env` |
| Frontend shows no data | Make sure the backend is running (`make dev-backend`) and you've run `make seed` |
| Tests fail with connection error | PostgreSQL must be running (`make db-up`) |
| Port 5432 already in use | Stop any local PostgreSQL: `brew services stop postgresql` or change the port in `docker-compose.yml` |
| Port 8000 already in use | Kill the process: `lsof -ti:8000 | xargs kill` |
