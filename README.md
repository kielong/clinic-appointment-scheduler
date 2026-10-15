# BAPC — Brownsville Adult & Pediatric Clinic

An appointment scheduling system for Brownsville Adult and Pediatric Clinic, with role-based
login, a chat scheduling assistant (LangGraph ReAct agent on Ollama), and an MCP server.

**Read these first:**
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — setup instructions and conventions

## Quick Start

```bash
make setup
cp backend/.env.example backend/.env

# Start the Ollama server:
ollama serve   # skip if using the Ollama desktop app

# In two terminals:
make dev-backend    # http://localhost:8000
make dev-frontend   # http://localhost:3000
```

Sign in at http://localhost:3000 with the development accounts created by `make seed`:

| Role | Email | Password |
|------|-------|----------|
| Admin / clinician | `admin@bapc.test` | `admin-dev-password` |
| Patient | `maria.garcia@example.com` | `patient-dev-password` |
| Patient | `james.wilson@example.com` | `patient-dev-password` |

Passwords come from `BAPC_SEED_*` in `backend/.env`. Create real staff logins with
`make create-admin email=you@clinic.org name="Your Name"`. Patients can also sign up at `/register`.

## Roles

| | Patient | Admin / clinician |
|---|---|---|
| Web pages | **Chat only** | Dashboard, Patients, Appointments, Chat |
| Chat can | See, book, reschedule, and cancel **their own** appointments | Look up any patient; book, reschedule, and cancel for anyone |
| REST API | `/api/auth/*`, `/api/chat` | Everything |

Authentication uses an httpOnly session cookie backed by the `auth_sessions` table (scrypt
password hashes, tokens stored as SHA-256). Access is enforced by the backend on every request;
the frontend guard only keeps users on pages they can use.

Patient registration links to an existing clinic record with the same email only when the date
of birth matches; otherwise the patient is asked to call the clinic.

## How chat scheduling works

1. The agent gets tools scoped to the signed-in user. A patient's tools are hard-wired to their own
   patient record, so the model never supplies a patient id.
2. Write tools (`schedule_appointment`, `cancel_appointment`, `reschedule_appointment`) **never
   write**. They validate the request and store a pending action in `chat_pending_actions`. The
   server shows the user an exact confirmation:
   ```
   Please confirm this booking:
   • Patient: Maria Garcia
   • Visit: Vaccination
   • When: Thursday, October 22, 2026 at 11:00 AM CDT
   • Duration: 30 min
   Reply "yes" to confirm or "no" to cancel.
   ```
3. When the user replies "yes" (or clicks **Confirm**), the chat route executes that stored action
   directly, re-validating it first. The language model cannot skip confirmation or change what was
   confirmed. "No", or any other message, discards it.
4. Dates and times are interpreted in the clinic's timezone. The tools check the model's
   arguments against the user's own words, so a hallucinated time or weekday is corrected. Write
   errors (e.g. "the clinic is closed on Saturdays") are relayed to the user verbatim.

### Scheduling rules (enforced for web, chat, and MCP alike)

All write paths go through `backend/app/services/scheduling.py`:

- Appointments must be in the future, at most one year ahead, and within clinic hours
  (Mon–Fri 8 AM–5 PM `America/Chicago` by default; configurable via `BAPC_CLINIC_*`).
- A patient cannot have two overlapping active (scheduled/rescheduled) appointments.
- Status changes follow `scheduled → rescheduled/completed/cancelled`,
  `rescheduled → completed/cancelled`, `cancelled → scheduled` (re-checked for conflicts).
  Completed visits are final.

### Database integrity

Enforced in PostgreSQL (migration `002`), not just in the app:

- CHECK constraints on appointment type, status, and duration (5–240 min); a role/patient-link
  CHECK on users.
- Case-insensitive unique emails for patients and users.
- `EXCLUDE USING gist` constraint: no overlapping active appointments per patient, even under
  concurrent writes.
- `ON DELETE CASCADE` from patients to appointments and to the patient's login (and its sessions).
- Migration `002` cleans existing rows before adding constraints: it trims and lowercases emails,
  fills NULL durations/statuses, and auto-cancels (with an audit note) any overlapping duplicates.
  If two patients share an email, it stops with a clear message instead of guessing.

## What's Included

### Models

- **Patient** — clinic patient with name, email, phone, date of birth, and notes
- **Appointment** — visit with type (`checkup`/`follow_up`/`sick_visit`/`physical`/`vaccination`/`new_patient`), date/time, duration, status, reason, and notes
- **User** — login with role `admin` or `patient` (patients link to exactly one Patient)
- **AuthSession**, **ChatPendingAction** — sessions and chat writes awaiting confirmation

### Agent Tools (`backend/app/agent/tools.py`)

| Patient tools | Admin tools |
|---|---|
| `my_appointments` | `search_patients`, `get_patient_details`, `list_appointments` |
| `schedule_appointment` (proposes) | `schedule_appointment` (proposes) |
| `cancel_appointment` (proposes) | `cancel_appointment` (proposes) |
| `reschedule_appointment` (proposes) | `reschedule_appointment` (proposes) |

### MCP Resources & Tools

The MCP server (`backend/app/mcp/server.py`, `make mcp-server`) runs locally over stdio with
direct database access, so it acts with **admin** privileges. MCP clients prompt for approval
before each tool call, so its write tools act immediately, using the same validation as above.

| URI | Description |
|-----|-------------|
| `bapc://patients` | JSON list of all registered patients |
| `bapc://appointments` | JSON list of upcoming active appointments |

**Tools**: `search_patients`, `get_patient_details`, `list_appointments`,
`get_appointment_details`, `schedule_appointment`, `cancel_appointment`, `reschedule_appointment`.

### Frontend Pages

- **Login / Register** (`/login`, `/register`)
- **Chat** (`/chat`) — the only page for patients; also available to admins
- **Dashboard** (`/`) — counts, next appointments, and past visits still marked scheduled
- **Patients** (`/patients`, `/patients/[id]`) — search, add, profile, appointment history
- **Appointments** (`/appointments`) — filters, manual scheduling form, mark completed / cancel
