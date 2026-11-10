"use client";
import { useState } from "react";
import { call } from "@/lib/client";
import { intl } from "@/lib/i18n";
import CopyText from "./CopyText";
import { useLocale, useT } from "./LocaleProvider";

type Created = { key: string; prefix: string; label: string; per_minute: number; per_day: number; expires_at: string };
type Info = { prefix: string; label: string; created_at: string; expires_at: string | null; last_used_at: string | null;
  used_today: number; per_minute: number; per_day: number };

/** Take a key (shown once), check its usage, revoke it. The key is only ever sent to the API. */
export default function DeveloperKeys({ api }: { api: string }) {
  const t = useT();
  const locale = useLocale();
  const day = (v: string | null) => (v ? new Date(v).toLocaleDateString(intl(locale), { dateStyle: "medium" }) : "–");
  const [label, setLabel] = useState("");
  const [accept, setAccept] = useState(false);
  const [created, setCreated] = useState<Created | null>(null);
  const [mine, setMine] = useState("");
  const [info, setInfo] = useState<Info | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const errorText = (code: string, message: string) => ({
    too_many_keys_today: t("No more keys today from here; try again tomorrow.", "Bugün buradan daha fazla anahtar alınamaz; yarın tekrar deneyin."),
    terms_not_accepted: t("Please accept the licence terms.", "Lütfen lisans koşullarını kabul edin."),
    invalid_api_key: t("This key is not valid, expired or revoked.", "Bu anahtar geçersiz, süresi dolmuş ya da iptal edilmiş."),
    not_a_developer_key: t("This is not a developer key.", "Bu bir geliştirici anahtarı değil."),
  } as Record<string, string>)[code] ?? message;

  async function take(e: React.FormEvent) {
    e.preventDefault();
    const r = await call<Created>(api, "/api/v1/developer/keys", { method: "POST", auth: false,
      body: JSON.stringify({ label, accept_terms: accept }) });
    if (r.ok) { setCreated(r.data); setMsg(null); } else setMsg({ ok: false, text: errorText(r.code, r.message) });
  }
  async function check(e: React.FormEvent) {
    e.preventDefault();
    const r = await call<Info>(api, "/api/v1/developer/key", { auth: false, headers: { Authorization: `Bearer ${mine.trim()}` } });
    if (r.ok) { setInfo(r.data); setMsg(null); } else { setInfo(null); setMsg({ ok: false, text: errorText(r.code, r.message) }); }
  }
  async function revoke() {
    if (!window.confirm(t("Revoke this key? Programs using it stop working at once.", "Bu anahtar iptal edilsin mi? Onu kullanan programlar hemen durur."))) return;
    const r = await call(api, "/api/v1/developer/key/revoke", { method: "POST", auth: false, headers: { Authorization: `Bearer ${mine.trim()}` } });
    if (r.ok) { setInfo(null); setMine(""); setMsg({ ok: true, text: t("The key is revoked.", "Anahtar iptal edildi.") }); }
    else setMsg({ ok: false, text: errorText(r.code, r.message) });
  }

  return (
    <div className="stack">
      {created ? (
        <div className="panel stack">
          <h3 style={{ margin: 0 }}>{t("Your key", "Anahtarınız")}</h3>
          <div className="notice error">{t("Copy it now: it is shown only once and cannot be recovered.",
                                           "Şimdi kopyalayın: yalnızca bir kez gösterilir, sonradan geri alınamaz.")}</div>
          <CopyText value={created.key} />
          <p className="muted">{t(`${created.per_minute} requests a minute, ${created.per_day.toLocaleString("en")} a day. Valid until ${day(created.expires_at)}.`,
                                  `Dakikada ${created.per_minute}, günde ${created.per_day.toLocaleString("tr-TR")} istek. ${day(created.expires_at)} tarihine kadar geçerli.`)}</p>
        </div>
      ) : (
        <form className="panel stack" onSubmit={take}>
          <h3 style={{ margin: 0 }}>{t("Take a Key", "Anahtar Alın")}</h3>
          <input type="text" maxLength={60} placeholder={t("Label (optional), e.g. my DNS filter", "Etiket (isteğe bağlı), ör. DNS filtrem")}
                 value={label} onChange={(e) => setLabel(e.target.value)} />
          <label className="muted"><input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} />{" "}
            {t("I will credit \"Cat-egory contributors\" (CC BY 4.0) when I use the data.",
               "Veriyi kullanırken \"Cat-egory contributors\" diye kaynak göstereceğim (CC BY 4.0).")}</label>
          <div className="row"><button type="submit" disabled={!accept}>{t("Take a Key", "Anahtar Al")}</button></div>
        </form>
      )}
      <form className="panel stack" onSubmit={check}>
        <h3 style={{ margin: 0 }}>{t("Check or Revoke Your Key", "Anahtarınızı Kontrol Edin veya İptal Edin")}</h3>
        <input type="password" autoComplete="off" placeholder="cat_…" value={mine} onChange={(e) => setMine(e.target.value)} />
        <div className="row"><button type="submit" className="secondary" disabled={!mine.trim()}>{t("Check", "Kontrol Et")}</button>
          {info && <button type="button" className="danger" onClick={revoke}>{t("Revoke", "İptal Et")}</button>}</div>
        {info && (
          <table><tbody>
            <tr><th>{t("Key", "Anahtar")}</th><td className="mono">cat_{info.prefix}_…{info.label && ` · ${info.label}`}</td></tr>
            <tr><th>{t("Used today", "Bugün kullanılan")}</th><td>{info.used_today.toLocaleString(intl(locale))} / {info.per_day.toLocaleString(intl(locale))}</td></tr>
            <tr><th>{t("Per minute", "Dakikada")}</th><td>{info.per_minute}</td></tr>
            <tr><th>{t("Last used", "Son kullanım")}</th><td>{day(info.last_used_at)}</td></tr>
            <tr><th>{t("Valid until", "Geçerlilik")}</th><td>{day(info.expires_at)}</td></tr>
          </tbody></table>
        )}
      </form>
      {msg && <div className={`notice ${msg.ok ? "ok" : "error"}`} role="status">{msg.text}</div>}
    </div>
  );
}
