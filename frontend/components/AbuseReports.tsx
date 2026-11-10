"use client";
import { Fragment, useCallback, useEffect, useState } from "react";
import { call } from "@/lib/client";
import KeyGate from "./KeyGate";
import CopyText from "./CopyText";
import { categoryName, intl } from "@/lib/i18n";
import { useLocale, useT } from "./LocaleProvider";

type Status = "draft" | "sent" | "dismissed" | "no_contact";
type Item = { id: number; domain: string; status: Status; kind: "registrar" | "hosting" | "none"; recipient: string | null;
  organisation: string | null; types: string[]; subject: string; body: string; created_at: string; decided_at: string | null;
  threat_active: boolean; web_form: string | null };
type Page = { counts: Record<Status, number>; items: Item[] };

/** mailto: opens the reviewer's own mail program with the draft filled in; nothing is sent from Cat-Egory. */
const mailto = (i: Item) => `mailto:${encodeURIComponent(i.recipient ?? "")}?subject=${encodeURIComponent(i.subject)}&body=${encodeURIComponent(i.body)}`;

function Reports({ api }: { api: string }) {
  const t = useT();
  const locale = useLocale();
  const [status, setStatus] = useState<Status | "all">("draft");
  const [data, setData] = useState<Page | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [msg, setMsg] = useState("");
  const LABEL: Record<Status, string> = { draft: t("To send", "Gönderilecek"), sent: t("Sent", "Gönderildi"),
    dismissed: t("Dismissed", "Vazgeçildi"), no_contact: t("No contact found", "Adres bulunamadı") };
  const KIND: Record<Item["kind"], string> = { registrar: t("Registrar", "Kayıt firması"), hosting: t("Hosting", "Barındırma"), none: "–" };
  const day = (v: string) => new Date(v).toLocaleString(intl(locale), { dateStyle: "medium", timeStyle: "short" });

  const load = useCallback(async () => {
    const r = await call<Page>(api, `/api/v1/admin/abuse-reports?status=${status}&limit=500`);
    if (r.ok) setData(r.data); else setMsg(`${r.message} (${r.code})`);
  }, [api, status]);
  useEffect(() => { void load(); }, [load]);

  async function decide(i: Item, action: "sent" | "dismiss") {
    const r = await call<Item>(api, `/api/v1/admin/abuse-reports/${i.id}/${action}`, { method: "POST" });
    setMsg(r.ok ? `${i.domain}: ${action === "sent" ? t("marked as sent", "gönderildi olarak işaretlendi") : t("dismissed", "vazgeçildi")}` : `${r.message} (${r.code})`);
    void load();
  }

  async function copyBody(i: Item) {
    try { await navigator.clipboard.writeText(i.body); setMsg(t("Text copied.", "Metin kopyalandı.")); }
    catch { setMsg(t("Copying is blocked here: select the text instead.", "Kopyalama engelli: metni seçerek kopyalayın.")); }
  }

  async function runNow() {
    const r = await call(api, "/api/v1/admin/abuse-reports/run", { method: "POST" });
    setMsg(r.ok ? t("Looking up contacts; new drafts appear in a minute or two.", "Adresler aranıyor; yeni taslaklar bir iki dakika içinde görünür.") : `${r.message} (${r.code})`);
  }

  if (!data) return <p className="muted">{msg || t("Loading…", "Yükleniyor…")}</p>;
  return (
    <div className="stack">
      <div className="row">
        <select value={status} aria-label={t("Show", "Göster")} onChange={(e) => setStatus(e.target.value as Status | "all")}>
          {(["draft", "sent", "no_contact", "dismissed"] as Status[]).map((s) => <option key={s} value={s}>{LABEL[s]} ({data.counts[s]})</option>)}
          <option value="all">{t("All", "Tümü")}</option>
        </select>
        <button className="secondary" onClick={runNow}>{t("Prepare Drafts Now", "Taslakları Şimdi Hazırla")}</button>
        {msg && <span className="muted">{msg}</span>}
      </div>
      {data.items.length === 0 ? <div className="notice">{t("Nothing here.", "Burada bir şey yok.")}</div> : (
        <div className="panel">
          <table>
            <thead><tr><th>{t("Domain", "Alan adı")}</th><th>{t("To", "Alıcı")}</th><th>{t("Finding", "Bulgu")}</th>
              <th>{t("Site now", "Site şu an")}</th><th></th></tr></thead>
            <tbody>{data.items.map((i) => (<Fragment key={i.id}>
              <tr>
                <td className="mono">{i.domain}<div className="muted" style={{ fontSize: 12 }}>{day(i.created_at)}</div></td>
                <td>{i.recipient ? <><span className="mono">{i.recipient}</span><div className="muted" style={{ fontSize: 12 }}>{KIND[i.kind]}{i.organisation && ` · ${i.organisation}`}</div>
                  {i.web_form && <div style={{ fontSize: 12 }}>{t("Prefers its ", "Mail yerine ")}<a href={i.web_form} target="_blank" rel="noopener noreferrer">{t("web form", "web formunu")}</a>{t(" to mail", " tercih ediyor")}</div>}</>
                  : <span className="muted">{t("No abuse address in RDAP — report through the provider's web form", "RDAP'ta abuse adresi yok — sağlayıcının web formundan bildirin")}</span>}</td>
                <td>{i.types.map((x) => categoryName(locale, x, x)).join(", ")}</td>
                <td>{i.threat_active ? <span className="badge danger">{t("Still flagged", "Hâlâ işaretli")}</span>
                  : <span className="badge ok">{t("Gone or withdrawn", "Kalktı ya da geri çekildi")}</span>}</td>
                <td className="row">
                  <button className="secondary" onClick={() => setOpen(open === i.id ? null : i.id)}>{open === i.id ? t("Hide", "Gizle") : t("Read", "Oku")}</button>
                  {i.status === "draft" && i.recipient && <a className="button secondary" href={mailto(i)} onClick={() => setOpen(i.id)}>{t("Open in Mail", "Postada Aç")}</a>}
                  {i.status === "draft" && <button onClick={() => decide(i, "sent")}>{t("I Sent It", "Gönderdim")}</button>}
                  {(i.status === "draft" || i.status === "no_contact") && <button className="secondary" onClick={() => decide(i, "dismiss")}>{t("Dismiss", "Vazgeç")}</button>}
                  {i.status !== "draft" && i.status !== "no_contact" && <span className="muted">{LABEL[i.status]}{i.decided_at && ` · ${day(i.decided_at)}`}</span>}
                </td>
              </tr>
              {open === i.id && (
                <tr><td colSpan={5}>
                  <div className="stack">
                    {i.recipient && <CopyText value={i.recipient} />}
                    <CopyText value={i.subject} />
                    <pre className="mail-body">{i.body}</pre>
                    <div><button className="secondary" onClick={() => copyBody(i)}>{t("Copy Text", "Metni Kopyala")}</button></div>
                  </div>
                </td></tr>
              )}
            </Fragment>))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function AbuseReports({ api }: { api: string }) {
  return <KeyGate api={api}>{() => <Reports api={api} />}</KeyGate>;
}
