# Court Signups

Live signup board for 6 badminton courts. Each court shows who's playing (max 4) and a line of waiting groups. You can start a new group or merge into any group that has room.

**Stack:** React (Vite) → FastAPI → Postgres

## Run locally

```bash
# backend
cd backend
pip install -r requirements.txt
export DATABASE_URL="postgresql://user:pass@localhost:5432/courts"
uvicorn main:app --reload          # http://localhost:8000/docs

# frontend (new terminal)
cd frontend
npm install
npm run dev                        # http://localhost:5173
```

Tables are created automatically on startup.

## Deploy for free

1. **Database: Neon** (neon.com). Create a project and copy the connection string, which looks like `postgresql://...neon.tech/neondb?sslmode=require`.
2. **API: Render** (render.com). Create a New Web Service from this repo with these settings:
   - Root directory: `backend`
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Environment variable: `DATABASE_URL` set to the Neon string
3. **Frontend: Vercel or Netlify**. Import the repo with these settings:
   - Root directory: `frontend`
   - Build command: `npm run build`
   - Output directory: `dist`
   - Environment variable: `VITE_API_URL` set to your Render URL (e.g. `https://court-signups.onrender.com`)
4. Optional: on Render, set `ALLOWED_ORIGINS` to your frontend URL so only your site can call the API.

Render's free tier sleeps after about 15 minutes idle, so the first load after a break takes around a minute. Opening the site a few minutes before people arrive avoids the wait.

## Design notes

- **Schema:** `groups(id, court, on_court, created_at)` and `members(id, group_id, name)`. The line order is `created_at`.
- **Invariants are enforced by the DB:**
  - A partial unique index allows only one playing group per court.
  - A unique index on `lower(name)` means a person can only be signed up once.
- **Concurrency:** every write takes `pg_advisory_xact_lock(court)` inside its transaction, so simultaneous clicks on the same court are serialized. This prevents 5-person groups and double promotions. A test with 10 people merging into a 1-person group at once results in exactly 3 joins.
- **Idempotent "done":** the endpoint takes the *group* id rather than the court. If two people tap "Done", the second call is a no-op instead of skipping the next group.
- **Sync:** the frontend polls `GET /courts` every 2 seconds. That's simple and good enough at this scale. The natural upgrade is a FastAPI WebSocket that pushes changes.
- **No auth:** this is an honor system, and anyone can remove anyone.
