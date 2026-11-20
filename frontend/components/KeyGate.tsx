"use client";
import { useEffect, useState } from "react";
import { call, getKey, setKey } from "@/lib/client";
import { useT } from "./LocaleProvider";

type Me = { user_id: number; role: string; scopes: string[] };

/** Admin pages: sign in once with email and password (a short-lived key, kept in this tab or for 7 days on this
 *  device) or with an API key. Every admin page shares the same stored key. */
export default function KeyGate({ api, children }: { api: string; children: (me: Me) => React.ReactNode }) {
  const t = useT();
  const [me, setMe] = useState<Me | null>(null);
  const [mode, setMode] = useState<"password" | "key">("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [needOtp, setNeedOtp] = useState(false);  // the password was right; the account has a second factor
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
      if (r.code === "invalid_api_key") { setKey(""); setError(t("Your sign-in has expired. Please sign in again.", "Oturumunuzun süresi doldu. Lütfen yeniden giriş yapın.")); }
      else setError(`${r.message} (${r.code})`);
    }
    setReady(true);
  }
  useEffect(() => { void check(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function signIn(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    const r = await call<{ key: string }>(api, "/api/v1/admin/session",
      { method: "POST", body: JSON.stringify({ email, password, remember, ...(needOtp ? { otp } : {}) }), auth: false });
    setBusy(false);
    setOtp("");
    if (!r.ok) {
      if (r.code === "otp_required") { setNeedOtp(true); setError(""); return; }  // keep the password for the second step
      setPassword("");
      setNeedOtp(r.code === "invalid_otp");
      setError(r.code === "invalid_login" ? t("Wrong email or password.", "E-posta veya parola yanlış.")
        : r.code === "invalid_otp" ? t("Wrong or already used code. Try the next code from the app.", "Kod yanlış ya da daha önce kullanılmış. Uygulamadaki bir sonraki kodu deneyin.")
        : r.code === "too_many_attempts" ? t("Too many attempts; try again in 15 minutes.", "Çok fazla deneme; 15 dakika sonra tekrar deneyin.")
        : r.code === "mfa_enrollment_required" ? t("This account needs a second factor first (mfa-enroll on the server).", "Bu hesap için önce iki adımlı doğrulama kurulmalı (sunucuda mfa-enroll).")
        : `${r.message} (${r.code})`);
      return;
    }
    setPassword("");
    setNeedOtp(false);
    setKey(r.data.key, remember);
    void check();
  }

  async function signOut() {
    await call(api, "/api/v1/admin/session/logout", { method: "POST" });  // revokes a sign-in key; API keys stay
    setKey("");
    setMe(null);
  }

  if (!ready) return <p className="muted">{t("Checking…", "Kontrol ediliyor…")}</p>;
  if (!me) {
    return mode === "password" ? (
      <form className="panel stack sign-in" onSubmit={signIn}>
        <h2 style={{ margin: 0 }}>{t("Sign In", "Giriş Yap")}</h2>
        <input type="email" autoComplete="username" placeholder={t("Email", "E-posta")} required value={email}
               onChange={(e) => setEmail(e.target.value)} />
        <input type="password" autoComplete="current-password" placeholder={t("Password", "Parola")} required value={password}
               onChange={(e) => setPassword(e.target.value)} />
        {needOtp && <input autoFocus inputMode="text" autoComplete="one-time-code" required value={otp} maxLength={14}
                           placeholder={t("6-digit code (or a backup code)", "6 haneli kod (ya da bir yedek kod)")}
                           aria-label={t("Authenticator code", "Doğrulama kodu")} onChange={(e) => setOtp(e.target.value)} />}
        <label className="muted"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />{" "}
          {t("Keep me signed in on this device for 7 days", "Bu cihazda 7 gün oturumum açık kalsın")}</label>
        <div className="row">
          <button type="submit" disabled={busy}>{t("Sign In", "Giriş Yap")}</button>
          <button type="button" className="secondary" onClick={() => { setMode("key"); setError(""); }}>{t("Use an API Key", "API Anahtarı Kullan")}</button>
        </div>
        {error && <span className="notice error">{error}</span>}
      </form>
    ) : (
      <form className="panel stack sign-in" onSubmit={(e) => { e.preventDefault(); setKey(input); setInput(""); void check(); }}>
        <p>{t("Enter an admin or moderator API key. It is kept only in this browser tab (sessionStorage).",
               "Bir yönetici ya da moderatör API anahtarı girin. Yalnızca bu tarayıcı sekmesinde saklanır (sessionStorage).")}</p>
        <input type="password" autoComplete="off" placeholder="cat_…" value={input} onChange={(e) => setInput(e.target.value)} />
        <div className="row">
          <button type="submit">{t("Use Key", "Anahtarı Kullan")}</button>
          <button type="button" className="secondary" onClick={() => { setMode("password"); setError(""); }}>{t("Sign In with Password", "Parolayla Giriş")}</button>
        </div>
        {error && <span className="notice error">{error}</span>}
      </form>
    );
  }
  return (
    <>
      <div className="row muted" style={{ fontSize: 13 }}>
        {t(`Signed in as user #${me.user_id} (${me.role})`, `Giriş yapan: kullanıcı #${me.user_id} (${me.role})`)} ·{" "}
        <button className="secondary" onClick={signOut}>{t("Sign Out", "Çıkış")}</button>
      </div>
      {children(me)}
    </>
  );
}
