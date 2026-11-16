"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { call } from "@/lib/client";
import KeyGate from "./KeyGate";
import { useLocale, useT } from "./LocaleProvider";
import { intl } from "@/lib/i18n";

type DomainDemand = { domain: string; lookups: number; callers: number; days: number; known: boolean;
  classification_status: string | null; primary: string | null };
type FeedDemand = { feed: string; downloads: number; callers: number };
type DayTotal = { day: string; lookups: number; domains: number };
type Data = { days: number; since: string; known: DomainDemand[]; unknown: DomainDemand[]; feeds: FeedDemand[];
  daily: DayTotal[]; min_callers: number };

function View({ api, canAdd }: { api: string; canAdd: boolean }) {
  const t = useT();
  const locale = useLocale();
  const [days, setDays] = useState(30);
  const [data, setData] = useState<Data | null>(null);
  const [msg, setMsg] = useState("");
  const [added, setAdded] = useState<Set<string>>(new Set());
  const n = (v: number) => v.toLocaleString(intl(locale));

  const load = useCallback(async () => {
    const r = await call<Data>(api, `/api/v1/admin/trends?days=${days}&limit=50`);
    if (r.ok) setData(r.data); else setMsg(`${r.message} (${r.code})`);
  }, [api, days]);
  useEffect(() => { void load(); }, [load]);

  async function add(domain: string) {
    const r = await call<{ created: number; scans_queued: number }>(api, "/api/v1/admin/domains", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domains: [domain], source: "demand", scan: "new", priority: 5 }) });
    if (r.ok) {
      setAdded((s) => new Set(s).add(domain));
      setMsg(t(`${domain} added; the scan is queued.`, `${domain} eklendi; tarama sıraya alındı.`));
    } else setMsg(`${r.message} (${r.code})`);
  }

  if (!data) return <p className="muted">{msg || t("Loading…", "Yükleniyor…")}</p>;
  const visitors = t("Visitors", "Ziyaretçi");
  const empty = <div className="notice">{t("Nothing counted in this period yet.", "Bu dönemde henüz sayılan bir şey yok.")}</div>;
  return (
    <div className="stack">
      <div className="row">
        <select value={days} aria-label={t("Period", "Dönem")} onChange={(e) => setDays(Number(e.target.value))}>
          {[1, 7, 30, 90].map((d) => <option key={d} value={d}>{t(`Last ${d} days`, `Son ${d} gün`)}</option>)}
        </select>
        <span className="muted">{t(`Since ${data.since}`, `${data.since} tarihinden beri`)}</span>
        {msg && <span className="muted">{msg}</span>}
      </div>

      <h2>{t("Looked Up, Not in the Database", "Sorgulanan Ama Veritabanında Olmayan")}</h2>
      <p className="muted">{t(
        `Names with at least ${data.min_callers} different visitors on one day are added and scanned automatically (a daily cap applies). "Visitors" adds up each day's distinct visitors.`,
        `Aynı gün en az ${data.min_callers} farklı ziyaretçinin sorguladığı adlar kendiliğinden eklenip taranır (günlük üst sınır var). "Ziyaretçi" her günün farklı ziyaretçi sayısının toplamıdır.`)}</p>
      {data.unknown.length === 0 ? empty : (
        <div className="panel"><table>
          <thead><tr><th>{t("Domain", "Alan adı")}</th><th className="num">{visitors}</th><th className="num">{t("Lookups", "Sorgu")}</th>
            <th className="num">{t("Days", "Gün")}</th><th></th></tr></thead>
          <tbody>{data.unknown.map((d) => (
            <tr key={d.domain}><td className="mono">{d.domain}</td><td className="num">{n(d.callers)}</td>
              <td className="num">{n(d.lookups)}</td><td className="num">{d.days}</td>
              <td>{added.has(d.domain) ? <span className="muted">{t("Added", "Eklendi")}</span>
                : canAdd && <button className="secondary" onClick={() => add(d.domain)}>{t("Add and Scan", "Ekle ve Tara")}</button>}</td></tr>
          ))}</tbody>
        </table></div>
      )}

      <h2>{t("Most Looked-Up Known Sites", "En Çok Sorgulanan Kayıtlı Siteler")}</h2>
      {data.known.length === 0 ? empty : (
        <div className="panel"><table>
          <thead><tr><th>{t("Domain", "Alan adı")}</th><th>{t("Category", "Kategori")}</th><th className="num">{visitors}</th>
            <th className="num">{t("Lookups", "Sorgu")}</th></tr></thead>
          <tbody>{data.known.map((d) => (
            <tr key={d.domain}><td><Link className="mono" href={`/domain/${encodeURIComponent(d.domain)}`}>{d.domain}</Link></td>
              <td>{d.primary ?? <span className="muted">{d.classification_status}</span>}</td>
              <td className="num">{n(d.callers)}</td><td className="num">{n(d.lookups)}</td></tr>
          ))}</tbody>
        </table></div>
      )}

      <h2>{t("List Downloads", "Liste İndirmeleri")}</h2>
      <p className="muted">{t("Requests include the \"not modified\" checks of firewalls that poll a list; the visitor count is the better measure of how many use it.",
        "İstek sayısına, listeyi düzenli yoklayan güvenlik duvarlarının \"değişmedi\" kontrolleri de dahildir; kaç kişinin kullandığını ziyaretçi sayısı daha iyi gösterir.")}</p>
      {data.feeds.length === 0 ? empty : (
        <div className="panel"><table>
          <thead><tr><th>{t("List", "Liste")}</th><th className="num">{visitors}</th><th className="num">{t("Requests", "İstek")}</th></tr></thead>
          <tbody>{data.feeds.map((f) => (
            <tr key={f.feed}><td className="mono">{f.feed}</td><td className="num">{n(f.callers)}</td><td className="num">{n(f.downloads)}</td></tr>
          ))}</tbody>
        </table></div>
      )}

      {data.daily.length > 0 && (<>
        <h2>{t("Per Day", "Günlük")}</h2>
        <div className="panel"><table>
          <thead><tr><th>{t("Day", "Gün")}</th><th className="num">{t("Lookups", "Sorgu")}</th><th className="num">{t("Distinct domains", "Farklı alan adı")}</th></tr></thead>
          <tbody>{[...data.daily].reverse().map((d) => (
            <tr key={d.day}><td>{d.day}</td><td className="num">{n(d.lookups)}</td><td className="num">{n(d.domains)}</td></tr>
          ))}</tbody>
        </table></div>
      </>)}
    </div>
  );
}

export default function Trends({ api }: { api: string }) {
  return <KeyGate api={api}>{(me) => <View api={api} canAdd={me.scopes.includes("domains:write")} />}</KeyGate>;
}
