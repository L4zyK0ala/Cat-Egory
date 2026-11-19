"use client";
import { useCallback, useEffect, useState } from "react";
import { call } from "@/lib/client";
import KeyGate from "./KeyGate";
import VtBadge, { vtEngines, type Vt } from "./VtBadge";
import { categoryName, intl } from "@/lib/i18n";
import { useLocale, useT } from "./LocaleProvider";

type Match = { id: number | string | null; value: string; type: string; category: string | null;
  category_name: string | null; criticality: number | null; date: string | null; scope: string; link: string | null };
type Item = { domain: string; types: string[]; first_seen: string; automatic?: boolean; status: "listed" | "missing" | "unchecked";
  sgb: { listed: boolean; checked_at: string; matches: Match[] } | null; vt?: Vt | null };
type Report = { counts: Record<string, number>; items: Item[] };
type Status = "missing" | "listed" | "unchecked" | "all";


export function Report({ api }: { api: string }) {
  const t = useT();
  const locale = useLocale();
  const LABEL: Record<Item["status"], string> = { listed: t("On the list", "Listede"), missing: t("Not on the list", "Listede değil"),
                                                 unchecked: t("Not checked yet", "Henüz bakılmadı") };
  const day = (v: string) => new Date(v).toLocaleDateString(intl(locale), { dateStyle: "medium" });
  const [status, setStatus] = useState<Status>("all");
  const [data, setData] = useState<Report | null>(null);
  const [msg, setMsg] = useState("");
  const [find, setFind] = useState("");
  const load = useCallback(async () => {
    const r = await call<Report>(api, `/api/v1/admin/threats/sgb-report?status=${status}&limit=1000`);
    if (r.ok) setData(r.data); else setMsg(`${r.message} (${r.code})`);
  }, [api, status]);
  useEffect(() => { void load(); }, [load]);

  /** Take back our own flags on a domain (a reviewer's mistake, or the site was cleaned). Kept as history. */
  async function withdraw(item: Item) {
    const reason = window.prompt(t(`Withdraw ${item.types.join(", ")} on ${item.domain}?\nReason (at least 10 characters):`,
                                   `${item.domain} üzerindeki ${item.types.join(", ")} geri çekilsin mi?\nNeden (en az 10 karakter):`),
                                 t("reviewer mistake: legitimate site", "inceleyici hatası: meşru site"));
    if (reason === null) return;
    if (reason.trim().length < 10) { setMsg(t("The reason needs at least 10 characters.", "Neden en az 10 karakter olmalı.")); return; }
    const r = await call<{ changed: number }>(api, "/api/v1/admin/threats/withdraw",
      { method: "POST", body: JSON.stringify({ domain: item.domain, reason: reason.trim() }) });
    setMsg(r.ok ? t(`${item.domain}: ${r.data.changed} flag(s) withdrawn.`, `${item.domain}: ${r.data.changed} işaret geri çekildi.`) : `${r.message} (${r.code})`);
    void load();
  }

  const shown = (data?.items ?? []).filter((i) => i.domain.includes(find.trim().toLowerCase()));

  async function copy() {
    const text = shown.map((i) => i.domain).join("\n");
    try { await navigator.clipboard.writeText(text); setMsg(t(`${shown.length} domains copied.`, `${shown.length} alan adı kopyalandı.`)); }
    catch { setMsg(t("Copying is not allowed in this browser; select the table instead.", "Bu tarayıcı kopyalamaya izin vermiyor; tabloyu seçin.")); }
  }

  if (!data) return <p className="muted">{msg || t("Loading…", "Yükleniyor…")}</p>;
  return (
    <div className="stack">
      <div className="row">
        <input type="search" placeholder={t("Find a domain", "Alan adı bul")} aria-label={t("Find a domain", "Alan adı bul")} value={find}
               onChange={(e) => setFind(e.target.value)} />
        <select value={status} aria-label={t("Show", "Göster")} onChange={(e) => setStatus(e.target.value as Status)}>
          <option value="all">{t("All confirmed threats", "Tüm onaylanmış tehditler")}</option>
          <option value="missing">{LABEL.missing} ({data.counts.missing})</option>
          <option value="listed">{LABEL.listed} ({data.counts.listed})</option>
          <option value="unchecked">{LABEL.unchecked} ({data.counts.unchecked})</option>
        </select>
        {status === "missing" && shown.length > 0 && <button className="secondary" onClick={copy}>{t("Copy Domains", "Alan Adlarını Kopyala")}</button>}
        {msg && <span className="muted">{msg}</span>}
      </div>
      {shown.length === 0 ? <div className="notice">{t("Nothing here.", "Burada bir şey yok.")}</div> : (
        <div className="panel">
          <table>
            <thead><tr><th>{t("Domain", "Alan adı")}</th><th>{t("Our finding", "Bulgumuz")}</th><th>{t("Confirmed", "Onay")}</th><th>{t("National list (SGB/USOM)", "Ulusal liste (SGB/USOM)")}</th><th>VirusTotal</th><th></th></tr></thead>
            <tbody>{shown.map((i) => (
              <tr key={i.domain}>
                <td className="mono">{i.domain}</td>
                <td>{i.types.map((x) => categoryName(locale, x, x)).join(", ")}{i.automatic && <div className="muted" style={{ fontSize: 12 }}>{t("automatic (Octryx)", "otomatik (Octryx)")}</div>}</td>
                <td>{day(i.first_seen)}</td>
                <td>
                  <span className={`badge ${i.status === "listed" ? "ok" : i.status === "missing" ? "danger" : "pending"}`}>{LABEL[i.status]}</span>
                  {i.sgb && <span className="muted" style={{ fontSize: 12 }}> · {t("checked", "kontrol")} {day(i.sgb.checked_at)}</span>}
                  {i.sgb?.matches.map((m) => (
                    <div key={`${m.id}-${m.value}`} style={{ fontSize: 13 }}>
                      {m.link ? <a href={m.link} target="_blank" rel="noopener noreferrer">{m.category_name ?? m.category}</a>
                        : (m.category_name ?? m.category)}
                      {m.criticality != null && ` · ${t("criticality", "kritiklik")} ${m.criticality}`}{m.date && ` · ${m.date.slice(0, 10)}`}
                      {m.scope !== "exact" && <span className="muted"> · {m.scope}: <span className="mono">{m.value}</span></span>}
                    </div>
                  ))}
                </td>
                <td>
                  <VtBadge vt={i.vt} />
                  {i.vt?.flagged && <div className="muted" style={{ fontSize: 12 }}>{vtEngines(i.vt)}</div>}
                </td>
                <td><button className="secondary" onClick={() => withdraw(i)}>{t("Withdraw", "Geri Çek")}</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function SgbReport({ api }: { api: string }) {
  return <KeyGate api={api}>{() => <Report api={api} />}</KeyGate>;
}
