"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { call } from "@/lib/client";
import KeyGate from "./KeyGate";
import { useLocale, useT } from "./LocaleProvider";
import { intl } from "@/lib/i18n";

type Measure = { rule: string; verdicts: number; positive: number; tp: number; fp: number; fn: number; tn: number;
  pending: number; precision: number | null; recall: number | null; listed: number; flagged: number; withdrawn: number };
type Gate = { auto: boolean; qualified: boolean; min_decisions: number; min_precision: number; max_per_day: number;
  flagged_today: number };
type Signals = { kind?: string | null; phone?: boolean; invest_terms?: string[]; secrets?: string[]; brand?: string | null; brand_via?: string | null; listed_brands?: string[];
  turkish?: boolean; official?: boolean; protected?: string | null; popular?: string | null;
  suspicious_name?: string | null; fresh_cert?: boolean };
type Verdict = { domain: string; positive: boolean; signals: Signals; action: string | null; created_at: string };
type Feed = { slug: string; enabled: boolean; last_success_at: string | null; last_error: string | null; listed: number;
  brand: number; tr: number; picked: number };
type Data = { measure: Measure; gate: Gate; recent: Verdict[]; feeds: Feed[]; per_hour: number };

function useFormat() {
  const locale = useLocale();
  return useMemo(() => {
    const pct = new Intl.NumberFormat(intl(locale), { style: "percent", maximumFractionDigits: 1 });
    const num = new Intl.NumberFormat(intl(locale));
    const when = new Intl.DateTimeFormat(intl(locale), { dateStyle: "short", timeStyle: "short", timeZone: "Europe/Istanbul" });
    return { pct: (v: number | null) => (v === null ? "–" : pct.format(v)), num: (v: number) => num.format(v),
             when: (v: string | null) => (v ? when.format(new Date(v)) : "–") };
  }, [locale]);
}

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return <div className="panel tile"><div className="label">{label}</div><div className="value">{value}</div>
    <div className="muted small">{sub}</div></div>;
}

function View({ api }: { api: string }) {
  const t = useT();
  const f = useFormat();
  const [data, setData] = useState<Data | null>(null);
  const [msg, setMsg] = useState("");
  const load = useCallback(async () => {
    const r = await call<Data>(api, "/api/v1/admin/detector?limit=50");
    if (r.ok) { setData(r.data); setMsg(""); } else setMsg(`${r.message} (${r.code})`);
  }, [api]);
  useEffect(() => { void load(); }, [load]);
  if (!data) return <p className="muted">{msg || t("Loading…", "Yükleniyor…")}</p>;

  const m = data.measure, g = data.gate;
  const judged = m.tp + m.fp;
  const progress = Math.min(1, judged / g.min_decisions);
  const secretName: Record<string, string> = { password: t("password", "şifre"), card: t("card", "kart"),
    sms_code: t("SMS code", "SMS kodu"), tr_id: t("TR ID no.", "TC kimlik no") };
  const reason = (s: Signals) => [
    s.kind === "investment_scam" ? t("investment scam", "yatırım dolandırıcılığı") : null,
    ...(s.secrets ?? []).map((x) => secretName[x] ?? x),
    s.phone ? t("phone number", "telefon numarası") : null,
    s.invest_terms?.length ? `${t("investment talk", "yatırım dili")}: ${s.invest_terms.slice(0, 3).join(", ")}` : null,
    s.brand ? `${t("brand", "marka")}: ${s.brand}${s.brand_via === "host" ? ` (${t("in the address", "adreste")})` : ""}` : null,
    s.listed_brands?.length ? `${t("lists brands", "marka listesi")}: ${s.listed_brands.join(", ")}` : null,
    s.suspicious_name ? s.suspicious_name : null,
    s.fresh_cert ? t("new certificate", "yeni sertifika") : null,
    s.official ? t("official site", "resmi site") : null,
    s.protected ? `${t("protected", "korumalı")}: ${s.protected}` : null,
    s.popular ? `${t("popular", "popüler")}: ${s.popular}` : null,
  ].filter(Boolean).join(" · ");
  const state = g.auto
    ? (g.qualified ? t("Flagging by itself", "Kendi başına işaretliyor") : t("Allowed, not proven yet", "İzinli, henüz kanıtlanmadı"))
    : t("Shadow mode (measuring only)", "Gölge mod (yalnız ölçüyor)");

  return (<>
    <div className="tiles">
      <Tile label={t("Status", "Durum")} value={state}
        sub={t(`up to ${g.max_per_day} flags a day; today ${g.flagged_today}`, `günde en fazla ${g.max_per_day} işaret; bugün ${g.flagged_today}`)} />
      <Tile label={t("Precision", "İsabet")} value={f.pct(m.precision)}
        sub={t(`${f.num(m.tp)} right of ${f.num(judged)} decided positives; needs ${f.pct(g.min_precision)}`,
               `karar verilen ${f.num(judged)} pozitiften ${f.num(m.tp)} doğru; gereken ${f.pct(g.min_precision)}`)} />
      <Tile label={t("Recall", "Yakalama")} value={f.pct(m.recall)}
        sub={t(`${f.num(m.fn)} threats people flagged that it missed`, `insanların işaretleyip kaçırdığı ${f.num(m.fn)} tehdit`)} />
      <Tile label={t("Waiting for a person", "İnsan kararı bekleyen")} value={f.num(m.pending)}
        sub={t(`${f.num(m.verdicts)} domains judged, ${f.num(m.positive)} positive`, `${f.num(m.verdicts)} alan adı, ${f.num(m.positive)} pozitif`)} />
    </div>

    <div className="panel">
      <strong>{t("Towards flagging by itself", "Kendi başına işaretlemeye doğru")}</strong>
      <p className="muted small">{t(
        `It may flag alone once people decided ${g.min_decisions} of its positive verdicts and at least ${f.pct(g.min_precision)} were right. Every 10th flag still goes to a person. Switch: CATEGORY_THREAT_DETECTOR_AUTO.`,
        `Pozitif kararlarından ${g.min_decisions} tanesine insan karar verip en az ${f.pct(g.min_precision)} doğru çıkınca tek başına işaretleyebilir. Her 10 işaretten biri yine insana gider. Anahtar: CATEGORY_THREAT_DETECTOR_AUTO.`)}</p>
      <span className="meter-cell"><span className="meter" aria-hidden="true"><span style={{ width: `${progress * 100}%` }} /></span>
        {f.num(judged)} / {f.num(g.min_decisions)}</span>
      <table className="small">
        <thead><tr><th></th><th className="num">{t("Person: threat", "İnsan: tehdit")}</th><th className="num">{t("Person: not a threat", "İnsan: tehdit değil")}</th></tr></thead>
        <tbody>
          <tr><td>{t("Laser Pointer: positive", "Lazer Pointer: pozitif")}</td><td className="num">{f.num(m.tp)}</td><td className="num">{f.num(m.fp)}</td></tr>
          <tr><td>{t("Laser Pointer: negative", "Lazer Pointer: negatif")}</td><td className="num">{f.num(m.fn)}</td><td className="num">{f.num(m.tn)}</td></tr>
        </tbody>
      </table>
      <p className="muted small">{t(
        `${f.num(m.listed)} of its positives are also on a threat list · ${f.num(m.flagged)} flags added by itself · ${f.num(m.withdrawn)} of those withdrawn`,
        `Pozitiflerinden ${f.num(m.listed)} tanesi bir tehdit listesinde de var · kendi eklediği ${f.num(m.flagged)} işaret · bunlardan geri alınan ${f.num(m.withdrawn)}`)}</p>
    </div>

    <h2>{t("Candidate Lists", "Aday Listeleri")}</h2>
    <p className="muted">{t(
      `Hosts these lists name are only candidates: ${data.per_hour} an hour are scanned, those resembling a Turkish brand first, then .tr names. Their verdicts are never published.`,
      `Bu listelerdeki adlar yalnız aday: saatte ${data.per_hour} tanesi taranır, önce Türk markasına benzeyenler, sonra .tr adlar. Listelerin hükmü hiçbir zaman yayımlanmaz.`)}</p>
    <div className="panel"><table>
      <thead><tr><th>{t("List", "Liste")}</th><th className="num">{t("Listed", "Listede")}</th>
        <th className="num">{t("Brand-like", "Markaya benzeyen")}</th><th className="num">.tr</th>
        <th className="num">{t("Scanned", "Taranan")}</th><th>{t("Last sync", "Son eşitleme")}</th></tr></thead>
      <tbody>{data.feeds.length === 0 && <tr><td colSpan={6} className="muted">{t("No list approved yet.", "Henüz onaylı liste yok.")}</td></tr>}
        {data.feeds.map((s) => (
          <tr key={s.slug}><td className="mono small">{s.slug}{!s.enabled && <> · {t("off", "kapalı")}</>}</td>
            <td className="num">{f.num(s.listed)}</td><td className="num">{f.num(s.brand)}</td><td className="num">{f.num(s.tr)}</td>
            <td className="num">{f.num(s.picked)}</td>
            <td>{s.last_error ? <span className="badge danger">{s.last_error}</span> : f.when(s.last_success_at)}</td></tr>
        ))}</tbody>
    </table></div>

    <h2>{t("Latest Verdicts", "Son Kararlar")}</h2>
    <div className="panel"><table>
      <thead><tr><th>{t("When", "Zaman")}</th><th>{t("Domain", "Alan adı")}</th><th>{t("Verdict", "Karar")}</th>
        <th>{t("Why", "Neden")}</th></tr></thead>
      <tbody>{data.recent.length === 0 && <tr><td colSpan={4} className="muted">{t("No verdict yet.", "Henüz karar yok.")}</td></tr>}
        {data.recent.map((v, i) => (
          <tr key={`${v.domain}-${i}`}><td className="small">{f.when(v.created_at)}</td>
            <td className="mono small"><a href={`/domain/${encodeURIComponent(v.domain)}`}>{v.domain}</a></td>
            <td><span className={`badge ${v.positive ? "danger" : "ok"}`}>{v.positive ? (v.signals.kind === "investment_scam" ? t("scam", "dolandırıcılık") : t("phishing", "oltalama")) : t("no proof", "kanıt yok")}</span>
              {v.action === "flagged" && <> <span className="badge pending">{t("flagged", "işaretledi")}</span></>}</td>
            <td className="small">{reason(v.signals) || "–"}</td></tr>
        ))}</tbody>
    </table></div>
  </>);
}

export default function Detector({ api }: { api: string }) {
  return <KeyGate api={api}>{() => <View api={api} />}</KeyGate>;
}
