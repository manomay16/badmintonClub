"""Badminton court signups API — FastAPI + Postgres.

Data model
  groups  : one row per group of players (on a court, or waiting in a court's line)
  members : one row per player, belongs to exactly one group

Concurrency: every write to a court runs in one transaction that first takes a
Postgres advisory lock keyed by court number, so two people clicking at the same
time on the same court are serialized (no 5-person groups, no double promotion).
"""
import os
from contextlib import asynccontextmanager, contextmanager

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

COURTS = 6
MAX_PER_GROUP = 4

pool = ConnectionPool(
    os.environ["DATABASE_URL"],
    kwargs={"row_factory": dict_row},
    check=ConnectionPool.check_connection,  # free DBs drop idle connections
    open=False,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    id         SERIAL PRIMARY KEY,
    court      INT NOT NULL CHECK (court BETWEEN 1 AND 6),
    on_court   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS members (
    id       SERIAL PRIMARY KEY,
    group_id INT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    name     TEXT NOT NULL
);
-- a player can only be signed up in one place (case-insensitive)
CREATE UNIQUE INDEX IF NOT EXISTS members_name_unique ON members (lower(name));
-- at most one group is playing on each court
CREATE UNIQUE INDEX IF NOT EXISTS one_playing_group_per_court ON groups (court) WHERE on_court;
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.open()
    with pool.connection() as conn:
        conn.execute(SCHEMA)
    yield
    pool.close()


app = FastAPI(title="Court Signups", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(psycopg.errors.UniqueViolation)
def taken_handler(request: Request, exc):
    return JSONResponse(status_code=409, content={"detail": "That name is already signed up somewhere."})


class Player(BaseModel):
    name: str = Field(min_length=1, max_length=24)


# ---------- helpers ----------

@contextmanager
def court_lock(court: int):
    """One transaction holding an exclusive lock on this court until commit/rollback."""
    if not 1 <= court <= COURTS:
        raise HTTPException(404, "No such court")
    with pool.connection() as conn:  # commits on success, rolls back on exception
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (court,))
        yield conn


def clean(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise HTTPException(422, "Name can't be blank")
    return name


def add_member(conn, group_id: int, name: str):
    if conn.execute("SELECT 1 FROM members WHERE lower(name) = lower(%s)", (name,)).fetchone():
        raise HTTPException(409, f"{name} is already signed up. Remove yourself first.")
    conn.execute("INSERT INTO members (group_id, name) VALUES (%s, %s)", (group_id, name))


def promote_next(conn, court: int):
    """If nobody is playing on this court, move the first group in line onto it."""
    if conn.execute("SELECT 1 FROM groups WHERE court = %s AND on_court", (court,)).fetchone():
        return
    conn.execute(
        """UPDATE groups SET on_court = TRUE
           WHERE id = (SELECT id FROM groups WHERE court = %s AND NOT on_court
                       ORDER BY created_at, id LIMIT 1)""",
        (court,),
    )


def court_of_group(group_id: int) -> int:
    with pool.connection() as conn:
        row = conn.execute("SELECT court FROM groups WHERE id = %s", (group_id,)).fetchone()
    if not row:
        raise HTTPException(404, "That group no longer exists")
    return row["court"]


# ---------- routes ----------

@app.get("/courts")
def list_courts():
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT g.id, g.court, g.on_court, array_agg(m.name ORDER BY m.id) AS names
               FROM groups g JOIN members m ON m.group_id = g.id
               GROUP BY g.id
               ORDER BY g.court, g.created_at, g.id"""
        ).fetchall()
    courts = [{"court": c, "on": None, "line": []} for c in range(1, COURTS + 1)]
    for r in rows:
        group = {"id": r["id"], "names": r["names"]}
        slot = courts[r["court"] - 1]
        if r["on_court"]:
            slot["on"] = group
        else:
            slot["line"].append(group)
    return courts


@app.post("/courts/{court}/join")
def join_court(court: int, p: Player):
    """Start a new group: goes straight onto the court if it's open, else to the back of the line."""
    name = clean(p.name)
    with court_lock(court) as conn:
        playing = conn.execute("SELECT 1 FROM groups WHERE court = %s AND on_court", (court,)).fetchone()
        gid = conn.execute(
            "INSERT INTO groups (court, on_court) VALUES (%s, %s) RETURNING id", (court, not playing)
        ).fetchone()["id"]
        add_member(conn, gid, name)
    return {"ok": True}


@app.post("/groups/{group_id}/join")
def join_group(group_id: int, p: Player):
    """Merge into an existing group (on the court or in line) if it has room."""
    name = clean(p.name)
    court = court_of_group(group_id)
    with court_lock(court) as conn:
        size = conn.execute(
            """SELECT count(m.id) AS n FROM groups g LEFT JOIN members m ON m.group_id = g.id
               WHERE g.id = %s GROUP BY g.id""",
            (group_id,),
        ).fetchone()
        if not size:
            raise HTTPException(404, "That group no longer exists")
        if size["n"] >= MAX_PER_GROUP:
            raise HTTPException(409, "That group is full")
        add_member(conn, group_id, name)
    return {"ok": True}


@app.post("/groups/{group_id}/done")
def finish_game(group_id: int):
    """The playing group is done: clear it and bring up the next group.
    Takes the group id (not the court) so a double-click can't skip the next group."""
    court = court_of_group(group_id)
    with court_lock(court) as conn:
        conn.execute("DELETE FROM groups WHERE id = %s AND on_court", (group_id,))
        promote_next(conn, court)
    return {"ok": True}


@app.post("/leave")
def leave(p: Player):
    name = clean(p.name)
    with pool.connection() as conn:
        row = conn.execute(
            """SELECT g.court FROM members m JOIN groups g ON g.id = m.group_id
               WHERE lower(m.name) = lower(%s)""",
            (name,),
        ).fetchone()
    if not row:
        raise HTTPException(404, f"{name} isn't signed up")
    with court_lock(row["court"]) as conn:
        gone = conn.execute(
            "DELETE FROM members WHERE lower(name) = lower(%s) RETURNING group_id", (name,)
        ).fetchone()
        if gone:  # drop the group if that was its last member
            conn.execute(
                "DELETE FROM groups g WHERE id = %s AND NOT EXISTS (SELECT 1 FROM members WHERE group_id = g.id)",
                (gone["group_id"],),
            )
        promote_next(conn, row["court"])
    return {"ok": True}


@app.post("/reset")
def reset():
    with pool.connection() as conn:
        conn.execute("TRUNCATE groups, members RESTART IDENTITY")
    return {"ok": True}
