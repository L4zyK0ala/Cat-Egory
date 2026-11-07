"use client";
import { useEffect, useState } from "react";
import { call, getKey, setKey } from "@/lib/client";

type Me = { user_id: number; role: string; scopes: string[] };

export default function KeyGate({ api, children }: { api: string; children: (me: Me) => React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);

  async function check() {
    if (!getKey()) {
      setMe(null);
      setReady(true);
      return;
    }
    const r = await call<Me>(api, "/api/v1/admin/me");
    if (r.ok) {
      setMe(r.data);
      setError("");
    } else {
      setMe(null);
      if (getKey()) setError(`${r.message} (${r.code})`);
    }
    setReady(true);
  }
  useEffect(() => { void check(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!ready) return <p className="muted">Checking…</p>;
  if (!me) {
    return (
      <form className="panel stack" onSubmit={(e) => { e.preventDefault(); setKey(input); setInput(""); void check(); }}>
        <p>Enter an admin or moderator API key. It is kept only in this browser tab (sessionStorage).</p>
        <input type="password" autoComplete="off" placeholder="cat_…" value={input} onChange={(e) => setInput(e.target.value)} />
        <div className="row"><button type="submit">Use key</button>{error && <span className="notice error">{error}</span>}</div>
      </form>
    );
  }
  return (
    <>
      <div className="row muted" style={{ fontSize: 13 }}>
        Signed in as user #{me.user_id} ({me.role}) ·{" "}
        <button className="secondary" onClick={() => { setKey(""); setMe(null); }}>Forget key</button>
      </div>
      {children(me)}
    </>
  );
}
