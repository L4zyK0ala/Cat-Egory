"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { call } from "@/lib/client";
import { categoryName, intl } from "@/lib/i18n";
import KeyGate from "./KeyGate";
import { useLocale, useT } from "./LocaleProvider";
import { Report as AllThreats } from "./SgbReport";

type Item = { domain: string; defanged: string; types: string[]; brand: string | null; confirmed_by: string[];
  first_seen: string | null; checked_at: string | null; status_at: string | null; listed_at: string | null;
  days_to_list: number | null; mail_id: number | null; sending: boolean; still_flagged: boolean };
type View = "pending" | "reported" | "listed" | "dismissed";
type Queue = { view: View; counts: Record<View | "unchecked", number>; median_days_to_list: number | null;
  mail: { ready: boolean; sender: string | null; recipient: string }; items: Item[] };
type Mail = { id: number; status: "queued" | "sent" | "failed"; recipient: string; domains: string[]; error: string | null };

const MAILTO_LIMIT = 1900;  // longer mailto: links are cut off by browsers and mail programs
const PER_MAIL = 100;  // the API's limit (threats/usom.py MAX_DOMAINS_PER_MAIL)
const BY_TR: Record<string, string> = { review: "inceleyici", detector: "sayfa dedektörü", octryx: "Octryx sayfa kanıtı" };

const trDay = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("tr-TR") : "–");

/** The e-mail is always in Turkish: it goes to the national CERT. */
function draft(items: Item[], site: string): { subject: string; body: string } {
  const lines = items.map((i) => {
    const types = i.types.map((x) => categoryName("tr", x, x)).join(", ");
    const brand = i.brand ? ` (${i.brand} taklidi)` : "";
    const by = i.confirmed_by.map((b) => BY_TR[b] ?? b).join(", ");
    return `- ${i.defanged} — ${types}${brand} · doğrulama: ${by} · ${trDay(i.first_seen)}`;
  });
  return {
    subject: `Zararlı Site Bildirimi [${new Date().toLocaleDateString("tr-TR")}]`,
    body: ["Merhaba,", "",
      `Cat-Egory (https://${site}) açık kaynaklı web kategorilendirme projesinde doğrulanan aşağıdaki alan adlarının ` +
      "zararlı bağlantılar listenizde yer almadığını gördük. İncelenmesini ve uygun görülürse listeye eklenmesini rica ederiz.",
      "", ...lines, "",
      "Adresler bilerek tıklanamaz biçimde ([.]) yazılmıştır.", "", "Saygılarımızla,", "Cat-Egory"].join("\n"),
  };
}

function Queue({ api, site }: { api: string; site: string }) {
  const t = useT();
  const locale = useLocale();
  const day = (v: string | null) => (v ? new Date(v).toLocaleDateString(intl(locale), { dateStyle: "medium" }) : "–");
  const [view, setView] = useState<View | "all">("pending");
  const [data, setData] = useState<Queue | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [subject, setSubject] = useState<string | null>(null);
  const [body, setBody] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const text = useRef<HTMLTextAreaElement>(null);

  const load = useCallback(async (v: View) => {
    const r = await call<Queue>(api, `/api/v1/admin/threats/usom?view=${v}`);
    if (r.ok) setData(r.data); else setMsg({ ok: false, text: `${r.message} (${r.code})` });
  }, [api]);
  useEffect(() => {
    setChosen(new Set()); setBody(null); setSubject(null);
    if (view !== "all") void load(view);
  }, [load, view]);

  const items = useMemo(() => (data && data.view === view ? data.items : []), [data, view]);
  const picked = items.filter((i) => chosen.has(i.domain));
  const generated = draft(picked, site);
  const mailSubject = subject ?? generated.subject;
  const mailBody = body ?? generated.body;

  function toggle(domain: string, on: boolean) {
    setChosen((s) => { const n = new Set(s); if (on && n.size < PER_MAIL) n.add(domain); else n.delete(domain); return n; });
    setBody(null);  // a new selection writes a new text; edits are kept only for the same selection
  }

  async function setStatus(domains: string[], action: "dismissed" | "pending") {
    const r = await call<{ changed: number }>(api, "/api/v1/admin/threats/usom/status",
      { method: "POST", body: JSON.stringify({ domains, action }) });
    setMsg(r.ok ? { ok: true, text: action === "dismissed" ? t(`${r.data.changed} set aside.`, `${r.data.changed} alan adı bildirilmeyecek olarak ayrıldı.`)
                                                         : t(`${r.data.changed} back in the queue.`, `${r.data.changed} alan adı kuyruğa geri alındı.`) }
                : { ok: false, text: `${r.message} (${r.code})` });
    if (view !== "all") void load(view);
  }

  async function send() {
    if (!data) return;
    if (!window.confirm(t(`Send the report about ${picked.length} domain(s) to ${data.mail.recipient}?`,
                          `${picked.length} alan adının bildirimi ${data.mail.recipient} adresine gönderilsin mi?`))) return;
    setBusy(true); setMsg(null);
    const r = await call<Mail>(api, "/api/v1/admin/threats/usom/send", { method: "POST",
      body: JSON.stringify({ domains: picked.map((i) => i.domain), subject: mailSubject, body: mailBody }) });
    if (!r.ok) { setBusy(false); setMsg({ ok: false, text: `${r.message} (${r.code})` }); return; }
    let mail = r.data;
    for (let i = 0; i < 30 && mail.status === "queued"; i++) {  // the worker sends it; wait up to a minute
      await new Promise((ok) => setTimeout(ok, 2000));
      const s = await call<Mail>(api, `/api/v1/admin/threats/usom/mails/${mail.id}`);
      if (s.ok) mail = s.data;
    }
    setBusy(false);
    setMsg(mail.status === "sent" ? { ok: true, text: t(`Sent to ${mail.recipient}: ${mail.domains.length} domain(s), now under Reported.`,
                                                      `${mail.recipient} adresine gönderildi: ${mail.domains.length} alan adı "Bildirildi" sekmesine geçti.`) }
      : mail.status === "failed" ? { ok: false, text: t(`Not sent: ${mail.error}`, `Gönderilemedi: ${mail.error}`) }
      : { ok: true, text: t("Still being sent; check the Reported tab shortly.", "Gönderim sürüyor; birazdan Bildirildi sekmesine bakın.") });
    setChosen(new Set()); setBody(null); setSubject(null);
    void load(view as View);
  }

  async function copy() {
    const value = `${mailSubject}\n\n${mailBody}`;
    try { await navigator.clipboard.writeText(value); setMsg({ ok: true, text: t("Text copied.", "Metin kopyalandı.") }); }
    catch {  // no clipboard API on plain http (the admin UI on the home network)
      text.current?.select();
      setMsg({ ok: true, text: document.execCommand("copy") ? t("Text copied.", "Metin kopyalandı.") : t("Select the text and copy it.", "Metni seçip kopyalayın.") });
    }
  }

  const c = data?.counts;
  const TABS: [View | "all", string][] = [
    ["pending", `${t("To report", "Bildirilecek")}${c ? ` (${c.pending})` : ""}`],
    ["reported", `${t("Reported", "Bildirildi")}${c ? ` (${c.reported})` : ""}`],
    ["listed", `${t("Listed by USOM", "USOM listeledi")}${c ? ` (${c.listed})` : ""}`],
    ["dismissed", `${t("Not to report", "Bildirilmeyecek")}${c ? ` (${c.dismissed})` : ""}`],
    ["all", t("All confirmed threats", "Tüm onaylanmış tehditler")],
  ];
  const mailto = data ? `mailto:${data.mail.recipient}?subject=${encodeURIComponent(mailSubject)}&body=${encodeURIComponent(mailBody)}` : "";

  return (
    <div className="stack">
      <div className="sub-tabs" role="group" aria-label={t("View", "Görünüm")}>
        {TABS.map(([v, label]) => <button key={v} type="button" aria-pressed={view === v} onClick={() => { setView(v); setMsg(null); }}>{label}</button>)}
      </div>
      {msg && <div className={`notice ${msg.ok ? "ok" : "error"}`} role="status">{msg.text}</div>}
      {view === "all" ? <AllThreats api={api} /> : !data ? <p className="muted">{t("Loading…", "Yükleniyor…")}</p> : <>
        {view === "pending" && <p className="muted" style={{ margin: 0 }}>
          {t("Threats we confirmed (a reviewer, the page detector or Octryx's page evidence) that the national list does not have. Phishing, scams, fraud and malware only; a look-alike name alone is not reported.",
             "Bizim onayladığımız (inceleyici, sayfa dedektörü ya da Octryx'in sayfa kanıtı) ama ulusal listede olmayan tehditler. Yalnızca oltalama, dolandırıcılık, sahtecilik ve zararlı yazılım; yalnızca taklit ad bildirilmez.")}
          {c && c.unchecked > 0 && t(` ${c.unchecked} more wait for the daily list check.`, ` ${c.unchecked} alan adı daha günlük liste kontrolünü bekliyor.`)}</p>}
        {view === "listed" && data.median_days_to_list != null && <div className="notice ok">
          {t(`USOM listed our reports in ${data.median_days_to_list} days (median).`, `USOM bildirimlerimizi ortanca ${data.median_days_to_list} günde listeledi.`)}</div>}
        {view === "pending" && !data.mail.ready && <div className="notice">
          {t("Sending is not set up (CATEGORY_USOM_MAIL_FROM and CATEGORY_RESEND_API_KEY in .env). You can still open the text in your mail program.",
             "Doğrudan gönderim ayarlı değil (.env içinde CATEGORY_USOM_MAIL_FROM ve CATEGORY_RESEND_API_KEY). Metni yine de posta programınızda açabilirsiniz.")}</div>}

        {items.length === 0 ? <div className="notice">{t("Nothing here.", "Burada bir şey yok.")}</div> : (
          <div className="panel">
            <table>
              <thead><tr>
                {view === "pending" && <th><input type="checkbox" aria-label={t(`Select the first ${PER_MAIL}`, `İlk ${PER_MAIL} alan adını seç`)}
                  title={t(`Select the first ${PER_MAIL} (one e-mail holds at most ${PER_MAIL})`, `İlk ${PER_MAIL} alan adını seç (bir e-postada en fazla ${PER_MAIL})`)}
                  checked={picked.length > 0 && picked.length === Math.min(PER_MAIL, items.filter((i) => !i.sending).length)}
                  onChange={(e) => { setChosen(e.target.checked ? new Set(items.filter((i) => !i.sending).slice(0, PER_MAIL).map((i) => i.domain)) : new Set()); setBody(null); }} /></th>}
                <th>{t("Domain", "Alan adı")}</th><th>{t("Our finding", "Bulgumuz")}</th>
                <th>{view === "pending" ? t("Confirmed", "Onay") : view === "dismissed" ? t("Set aside", "Ayrıldı") : t("Reported", "Bildirim")}</th>
                <th>{view === "listed" ? t("Listed", "Listelendi") : t("List checked", "Liste kontrolü")}</th><th></th>
              </tr></thead>
              <tbody>{items.map((i) => (
                <tr key={i.domain}>
                  {view === "pending" && <td><input type="checkbox" disabled={i.sending || (!chosen.has(i.domain) && chosen.size >= PER_MAIL)} checked={chosen.has(i.domain)}
                    aria-label={i.domain} onChange={(e) => toggle(i.domain, e.target.checked)} /></td>}
                  <td className="mono">{i.domain}{i.sending && <div className="muted" style={{ fontSize: 12 }}>{t("being sent…", "gönderiliyor…")}</div>}</td>
                  <td>{i.types.map((x) => categoryName(locale, x, x)).join(", ") || <span className="muted">{t("flag withdrawn", "işaret kalktı")}</span>}
                    {i.brand && <div className="muted" style={{ fontSize: 12 }}>{t(`imitates ${i.brand}`, `${i.brand} taklidi`)}</div>}
                    <div className="muted" style={{ fontSize: 12 }}>{i.confirmed_by.map((b) => t(b, BY_TR[b] ?? b)).join(", ")}</div></td>
                  <td>{day(view === "pending" ? i.first_seen : i.status_at)}{i.mail_id != null && <div className="muted" style={{ fontSize: 12 }}>{t("mail", "posta")} #{i.mail_id}</div>}</td>
                  <td>{view === "listed" ? <>{day(i.listed_at)}<div className="muted" style={{ fontSize: 12 }}>{t(`${i.days_to_list} days after`, `${i.days_to_list} gün sonra`)}</div></> : day(i.checked_at)}</td>
                  <td>{view === "pending" ? <button className="secondary" onClick={() => void setStatus([i.domain], "dismissed")}>{t("Not to Report", "Bildirme")}</button>
                    : view !== "listed" && <button className="secondary" onClick={() => void setStatus([i.domain], "pending")}>{t("Back to Queue", "Kuyruğa Al")}</button>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}

        {view === "pending" && picked.length > 0 && (
          <div className="panel stack usom-compose">
            <h2 style={{ margin: 0 }}>{t(`Report ${picked.length} domain(s) to USOM`, `${picked.length} alan adını USOM'a bildir`)}</h2>
            {picked.length >= PER_MAIL && <div className="muted" style={{ fontSize: 13 }}>{t(`One e-mail holds at most ${PER_MAIL} domains; send the rest in the next one.`,
                                                                                       `Bir e-postada en fazla ${PER_MAIL} alan adı olur; kalanları sonraki e-postayla gönderin.`)}</div>}
            <div className="muted" style={{ fontSize: 13 }}>{t("To", "Alıcı")}: <span className="mono">{data.mail.recipient}</span>
              {data.mail.sender && <> · {t("from", "gönderen")}: <span className="mono">{data.mail.sender}</span></>}</div>
            <input type="text" value={mailSubject} onChange={(e) => setSubject(e.target.value)} aria-label={t("Subject", "Konu")} />
            <textarea ref={text} value={mailBody} onChange={(e) => setBody(e.target.value)} rows={Math.min(22, picked.length + 12)} aria-label={t("Text", "Metin")} />
            <div className="row">
              <button disabled={busy || !data.mail.ready} onClick={() => void send()}>{busy ? t("Sending…", "Gönderiliyor…") : t("Send to USOM", "USOM'a Gönder")}</button>
              {mailto.length <= MAILTO_LIMIT && <a className="button secondary" href={mailto}>{t("Open in Mail Program", "Posta Programında Aç")}</a>}
              <button className="secondary" onClick={() => void copy()}>{t("Copy Text", "Metni Kopyala")}</button>
              <button className="secondary" onClick={() => void setStatus(picked.map((i) => i.domain), "dismissed")}>{t("Set Aside", "Bildirme")}</button>
            </div>
            {!data.mail.ready && <div className="muted" style={{ fontSize: 13 }}>{t("After sending it yourself, the domains stay here; set them aside or wait for the list to add them.",
                                                                                 "Kendiniz gönderdikten sonra alan adları burada kalır; listeye eklenince kendiliğinden düşerler.")}</div>}
          </div>
        )}
      </>}
    </div>
  );
}

export default function UsomQueue({ api, site }: { api: string; site: string }) {
  return <KeyGate api={api}>{() => <Queue api={api} site={site} />}</KeyGate>;
}
