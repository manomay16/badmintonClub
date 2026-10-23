import { useCallback, useEffect, useState } from "react";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";
const MAX = 4;
const POLL_MS = 2000;

export default function App() {
  const [courts, setCourts] = useState([]);
  const [name, setName] = useState(() => localStorage.getItem("name") || "");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/courts`);
      setCourts(await res.json());
      setError("");
    } catch {
      setError("Can't reach the server (it may be waking up — give it a minute).");
    }
  }, []);

  // Poll so everyone's screen stays in sync.
  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => localStorage.setItem("name", name), [name]);

  async function post(path, body) {
    const res = await fetch(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const { detail } = await res.json().catch(() => ({}));
      alert(typeof detail === "string" ? detail : "Something went wrong");
    }
    load();
  }

  function asMe(path) {
    const n = name.trim();
    if (!n) return alert("Enter your name first");
    post(path, { name: n });
  }

  function renderGroup(g) {
    return (
      <>
        {g.names.map((n) => (
          <span className="chip" key={n}>
            {n}
            <button title="Remove" onClick={() => confirm(`Remove ${n}?`) && post("/leave", { name: n })}>×</button>
          </span>
        ))}
        {g.names.length < MAX && <button onClick={() => asMe(`/groups/${g.id}/join`)}>+ Merge</button>}
      </>
    );
  }

  return (
    <>
      <h1>🏸 Court Signups</h1>
      <label>
        Your name: <input value={name} maxLength={24} placeholder="Name" onChange={(e) => setName(e.target.value)} />
      </label>
      {error && <p className="err">{error}</p>}

      <div className="grid">
        {courts.map((c) => (
          <div className="court" key={c.court}>
            <h2>
              Court {c.court}
              {c.on && (
                <button onClick={() => confirm(`Court ${c.court} finished?`) && post(`/groups/${c.on.id}/done`)}>
                  Done → next
                </button>
              )}
            </h2>

            <div className="grp on">
              <small>On court ({c.on ? c.on.names.length : 0}/{MAX}): </small>
              {c.on ? renderGroup(c.on) : <button onClick={() => asMe(`/courts/${c.court}/join`)}>Court open — take it</button>}
            </div>

            <small>Line: {c.line.length ? `${c.line.length} group(s)` : "empty"}</small>
            {c.line.map((g, i) => (
              <div className="grp" key={g.id}>
                <small>#{i + 1} ({g.names.length}/{MAX}): </small>
                {renderGroup(g)}
              </div>
            ))}
            {c.on && <button onClick={() => asMe(`/courts/${c.court}/join`)}>+ Get in line (new group)</button>}
          </div>
        ))}
      </div>

      <button className="reset" onClick={() => confirm("Clear ALL courts and lines?") && post("/reset")}>
        Reset all courts
      </button>
    </>
  );
}
