"use client";
import { useEffect, useState } from "react";
import { call, getKey, setKey } from "@/lib/client";

type Me = { user_id: number; role: string; scopes: string[] };

/** Admin pages: sign in once with email and password (a short-lived key, kept in this tab or for 7 days on this
 *  device) or with an API key. Every admin page shares the same stored key. */
export default function KeyGate({ api, children }: { api: string; children: (me: Me) => React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [mode, setMode] = useState<"password" | "key">("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(false);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
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
      if (r.code === "invalid_api_key") { setKey(""); setError("Your sign-in has expired. Please sign in again."); }
      else setError(`${r.message} (${r.code})`);
    }
    setReady(true);
  }
  useEffect(() => { void check(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function signIn(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const r = await call<{ key: string }>(api, "/api/v1/admin/session",
      { method: "POST", body: JSON.stringify({ email, password, remember }), auth: false });
    setBusy(false);
    setPassword("");
    if (!r.ok) { setError(r.code === "invalid_login" ? "Wrong email or password." : `${r.message} (${r.code})`); return; }
    setKey(r.data.key, remember);
    void check();
  }

  async function signOut() {
    await call(api, "/api/v1/admin/session/logout", { method: "POST" });  // revokes a sign-in key; API keys stay
    setKey("");
    setMe(null);
  }

  if (!ready) return <p className="muted">Checking…</p>;
  if (!me) {
    return mode === "password" ? (
      <form className="panel stack sign-in" onSubmit={signIn}>
        <h2 style={{ margin: 0 }}>Sign In</h2>
        <input type="email" autoComplete="username" placeholder="Email" required value={email}
               onChange={(e) => setEmail(e.target.value)} />
        <input type="password" autoComplete="current-password" placeholder="Password" required value={password}
               onChange={(e) => setPassword(e.target.value)} />
        <label className="muted"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />{" "}
          Keep me signed in on this device for 7 days</label>
        <div className="row">
          <button type="submit" disabled={busy}>Sign In</button>
          <button type="button" className="secondary" onClick={() => { setMode("key"); setError(""); }}>Use an API Key</button>
        </div>
        {error && <span className="notice error">{error}</span>}
      </form>
    ) : (
      <form className="panel stack sign-in" onSubmit={(e) => { e.preventDefault(); setKey(input); setInput(""); void check(); }}>
        <p>Enter an admin or moderator API key. It is kept only in this browser tab (sessionStorage).</p>
        <input type="password" autoComplete="off" placeholder="cat_…" value={input} onChange={(e) => setInput(e.target.value)} />
        <div className="row">
          <button type="submit">Use Key</button>
          <button type="button" className="secondary" onClick={() => { setMode("password"); setError(""); }}>Sign In with Password</button>
        </div>
        {error && <span className="notice error">{error}</span>}
      </form>
    );
  }
  return (
    <>
      <div className="row muted" style={{ fontSize: 13 }}>
        Signed in as user #{me.user_id} ({me.role}) ·{" "}
        <button className="secondary" onClick={signOut}>Sign Out</button>
      </div>
      {children(me)}
    </>
  );
}
