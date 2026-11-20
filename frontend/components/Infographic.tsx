"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { call } from "@/lib/client";
import { categoryName, intl, makeT, type Locale, type T } from "@/lib/i18n";
import KeyGate from "./KeyGate";
import { useLocale, useT } from "./LocaleProvider";

type Count = { key: string; count: number };
export type Data = {
  month: string; start: string; end: string; complete: boolean;
  totals: { published: number; new_published: number; verified: number; tr_published: number; categories_used: number };
  categories: Count[]; tr_categories: Count[];
  threats: { new_domains: number; own_domains: number; by_type: Count[]; brands: Count[]; suffixes: Count[] };
  demand: { lookups: number; domains: number; feed_downloads: number; by_category: Count[]; daily: Count[] };
  generated_at: string;
  funnel: { known: number; new_known: number; scanned: number; published: number; verified: number };
  threat_flow: { candidates: number; decided: number; confirmed: number; labels: number };
  ai: { answers: number; valid: number; models: string[]; sample_n: number; sample_kept: number; sample_low: number | null;
        sample_high: number | null; all_sample_n: number; all_sample_kept: number; hard_n: number; hard_kept: number };
  crawler: { jobs: number; succeeded: number; failed: number; blocked: number; unique_succeeded: number; retried: number;
             recovered: number; errors: Count[]; daily: Count[] };
  usage: { feed_downloads: number; feed_clients: number; formats: Count[]; lists: Count[]; lookups: number; lookup_daily: Count[] };
};
export type Cat = { id: string; name: string };

// A fixed, light card: it is an image for LinkedIn and other feeds (4:5 portrait), not part of the themed page.
const W = 1080, H = 1350, PAD = 80;
const C = { bg: "#FBF6EE", panel: "#FFFDF9", beige: "#F4E8D6", line: "#E3D3BD", text: "#2A1E1A", muted: "#5C4A42",
            red: "#C8352B", redSoft: "#F6D9D3" };
// Fonts a browser has without loading anything: an SVG drawn into a canvas cannot use the page's web fonts.
const SERIF = "Georgia, 'DejaVu Serif', 'Times New Roman', serif";
const SANS = "Arial, 'Helvetica Neue', 'DejaVu Sans', sans-serif";

type Ctx = { lang: Locale; t: T; n: (v: number) => string; pct: (part: number, whole: number) => string;
             cat: (id: string) => string; month: string; site: string };

function months(today = new Date()): string[] {
  return Array.from({ length: 12 }, (_, i) => {
    const d = new Date(today.getFullYear(), today.getMonth() - i, 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  });
}

function monthName(month: string, lang: Locale) {
  const [y, m] = month.split("-").map(Number);
  const s = new Date(y, m - 1, 1).toLocaleDateString(intl(lang), { month: "long", year: "numeric" });
  return s.charAt(0).toLocaleUpperCase(intl(lang)) + s.slice(1);
}

/** The Cat-Egory icon (app/icon.svg), drawn at (x, y) with the given size. */
function Icon({ x, y, size, id }: { x: number; y: number; size: number; id: string }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${size / 64})`}>
      <defs><clipPath id={id}><ellipse cx="32" cy="36" rx="23" ry="20" /></clipPath></defs>
      <path d="M12 26 8 6l16 11M52 26l4-20-16 11" fill="#858C97" stroke={C.text} strokeWidth="3.5" strokeLinejoin="round" />
      <path d="M13 20 11 11l8 6zM51 20l2-9-8 6z" fill="#EFAFB0" />
      <ellipse cx="32" cy="36" rx="23" ry="20" fill="#858C97" />
      <path clipPath={`url(#${id})`} d="M32 30c-1 4-2 7-6 9-5 3-10 5-11 12h34c-1-7-6-9-11-12-4-2-5-5-6-9z" fill="#FBFAF7" />
      <ellipse cx="32" cy="36" rx="23" ry="20" fill="none" stroke={C.text} strokeWidth="3.5" />
      <circle cx="23" cy="33" r="6" fill="#B9C96B" stroke={C.text} strokeWidth="2.5" />
      <circle cx="41" cy="33" r="6" fill="#B9C96B" stroke={C.text} strokeWidth="2.5" />
      <circle cx="23.8" cy="33.6" r="3" fill={C.text} />
      <circle cx="41.8" cy="33.6" r="3" fill={C.text} />
      <path d="M29.5 41h5l-2.5 3z" fill="#E8949A" stroke={C.text} strokeWidth="1.2" strokeLinejoin="round" />
    </g>
  );
}

/** Split a footnote into lines of at most ``max`` characters (SVG text does not wrap by itself). */
function wrap(text: string, max: number): string[] {
  const out: string[] = [];
  let line = "";
  for (const word of text.split(" ")) {
    if (line && (line + " " + word).length > max) { out.push(line); line = word; } else line = line ? `${line} ${word}` : word;
  }
  return line ? [...out, line] : out;
}

/** Header, footer, page number and the card's footnote (what was measured, from where, when). */
function Frame({ c, page, pages, id, note, children }: { c: Ctx; page: number; pages: number; id: string; note: string;
  children: React.ReactNode }) {
  const lines = wrap(note, 92).slice(0, 3);
  return (<>
    <rect width={W} height={H} fill={C.bg} />
    <Icon x={PAD} y={64} size={72} id={`icon-${id}`} />
    <text x={PAD + 88} y={118} fontFamily={SERIF} fontSize="42" fontWeight="700" fill={C.text}>
      Cat-<tspan fill={C.red}>Egory</tspan></text>
    <text x={W - PAD} y={116} textAnchor="end" fontFamily={SANS} fontSize="28" fill={C.muted}>{monthName(c.month, c.lang)}</text>
    <line x1={PAD} x2={W - PAD} y1={168} y2={168} stroke={C.line} strokeWidth="2" />
    {children}
    {lines.map((l, i) => <text key={i} x={PAD} y={H - 122 - (lines.length - 1 - i) * 24} fontFamily={SANS} fontSize="19"
                                fill={C.muted}>{l}</text>)}
    <line x1={PAD} x2={W - PAD} y1={H - 110} y2={H - 110} stroke={C.line} strokeWidth="2" />
    <text x={PAD} y={H - 62} fontFamily={SANS} fontSize="26" fill={C.muted}>
      <tspan fontWeight="700" fill={C.text}>{c.site}</tspan> · {c.t("open data, CC BY 4.0", "açık veri, CC BY 4.0")}</text>
    <text x={W - PAD} y={H - 62} textAnchor="end" fontFamily={SANS} fontSize="26" fill={C.muted}>{page}/{pages}</text>
  </>);
}

function Title({ lines, y = 270, sub }: { lines: string[]; y?: number; sub?: string }) {
  return (<>
    {lines.map((l, i) => <text key={i} x={PAD} y={y + i * 76} fontFamily={SERIF} fontSize="66" fontWeight="700" fill={C.text}>{l}</text>)}
    {sub && <text x={PAD} y={y + lines.length * 76 - 8} fontFamily={SANS} fontSize="28" fill={C.muted}>{sub}</text>}
  </>);
}

/** Horizontal bars, one series: the label above its bar, the value at the right end. */
function Bars({ items, y, label, value, rowH = 92 }: { items: Count[]; y: number; label: (k: string) => string;
  value: (it: Count) => string; rowH?: number }) {
  const max = Math.max(1, ...items.map((i) => i.count));
  const width = W - 2 * PAD;
  return (<>{items.map((it, i) => {
    const top = y + i * rowH;
    const w = Math.max(8, (it.count / max) * width);
    return (
      <g key={it.key}>
        <text x={PAD} y={top} fontFamily={SANS} fontSize="30" fill={C.text}>{label(it.key)}</text>
        <text x={W - PAD} y={top} textAnchor="end" fontFamily={SANS} fontSize="30" fontWeight="700" fill={C.text}>{value(it)}</text>
        <rect x={PAD} y={top + 16} width={width} height={26} rx="4" fill={C.beige} />
        <rect x={PAD} y={top + 16} width={w} height={26} rx="4" fill={it.key === "other" ? "#B8A898" : C.red} />
      </g>
    );
  })}</>);
}

function Stat({ x, y, w, h, value, label }: { x: number; y: number; w: number; h: number; value: string; label: string[] }) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx="24" fill={C.panel} stroke={C.line} strokeWidth="2" />
      <text x={x + 36} y={y + 108} fontFamily={SERIF} fontSize="80" fontWeight="700" fill={C.red}>{value}</text>
      {label.map((l, i) => <text key={i} x={x + 36} y={y + 164 + i * 38} fontFamily={SANS} fontSize="30" fill={C.muted}>{l}</text>)}
    </g>
  );
}

function Empty({ text, y }: { text: string; y: number }) {
  return <text x={PAD} y={y} fontFamily={SANS} fontSize="32" fill={C.muted}>{text}</text>;
}

function Cover({ d, c }: { d: Data; c: Ctx }) {
  const { t, n } = c;
  const w = (W - 2 * PAD - 32) / 2, h = 250;
  return (<>
    <text x={PAD} y={300} fontFamily={SANS} fontSize="32" fontWeight="700" fill={C.red} letterSpacing="4">
      {t("MONTHLY REPORT", "AYLIK BÜLTEN")}</text>
    <Title y={392} lines={[monthName(d.month, c.lang)]} />
    <text x={PAD} y={460} fontFamily={SANS} fontSize="32" fill={C.muted}>
      {t("Website categories and threats, from Türkiye", "Türkiye'den site kategorileri ve tehditler")}</text>
    <Stat x={PAD} y={530} w={w} h={h} value={n(d.funnel.known)} label={t("known,domains", "bilinen,alan adı").split(",")} />
    <Stat x={PAD + w + 32} y={530} w={w} h={h} value={n(d.funnel.new_known)} label={t("discovered,this month", "bu ay,keşfedilen").split(",")} />
    <Stat x={PAD} y={530 + h + 32} w={w} h={h} value={n(d.totals.new_published)} label={t("newly categorized,this month", "bu ay yeni,kategorilendirilen").split(",")} />
    <Stat x={PAD + w + 32} y={530 + h + 32} w={w} h={h} value={n(d.threats.new_domains)} label={t("new threat,domains", "yeni tehdit,alan adı").split(",")} />
    <text x={PAD} y={530 + 2 * h + 32 + 90} fontFamily={SANS} fontSize="30" fill={C.muted}>
      {t(`${n(d.totals.published)} published · ${n(d.totals.categories_used)} categories · ${n(d.totals.verified)} checked by a person`,
         `${n(d.totals.published)} site yayında · ${n(d.totals.categories_used)} kategori · ${n(d.totals.verified)} insan doğrulamalı`)}</text>
  </>);
}

function TrCategories({ d, c }: { d: Data; c: Ctx }) {
  const { t, n, pct } = c;
  const whole = d.totals.tr_published;
  return (<>
    <Title lines={t("What are Türkiye's,.tr websites about?", "Türkiye'nin .tr siteleri,hangi kategorilerde?").split(",")}
           sub={t(`Main category of ${n(whole)} published .tr websites`, `Yayındaki ${n(whole)} .tr sitesinin ana kategorisi`)} />
    {d.tr_categories.length === 0 ? <Empty y={520} text={t("No .tr websites published yet.", "Henüz yayında .tr sitesi yok.")} /> :
      <Bars y={500} rowH={72} items={d.tr_categories.slice(0, 9)} label={c.cat} value={(it) => `${pct(it.count, whole)}  ·  ${n(it.count)}`} />}
  </>);
}

function Threats({ d, c }: { d: Data; c: Ctx }) {
  const { t, n } = c;
  const th = d.threats;
  if (th.new_domains === 0) return (<>
    <Title lines={t("Threats,this month", "Bu ayın,tehditleri").split(",")} />
    <Empty y={520} text={t("No new threat records this month.", "Bu ay yeni tehdit kaydı yok.")} />
  </>);
  const types = th.by_type.slice(0, 4);
  const flowY = 560 + types.length * 72 + 30;
  const brandsY = flowY + 110;
  let bx = PAD, by = brandsY + 40;
  const suffixY = H - 200;
  const f = d.threat_flow;
  const pills = th.brands.slice(0, 8).map((b) => {
    const text = `${b.key}  ${n(b.count)}`;
    const w = text.length * 14.5 + 48;
    if (bx + w > W - PAD) { bx = PAD; by += 70; }
    const pill = { ...b, text, x: bx, y: by, w };
    bx += w + 16;
    return pill;
  }).filter((p) => p.y + 52 <= suffixY - 36);  // only the rows that fit above the endings line
  return (<>
    <text x={PAD} y={300} fontFamily={SERIF} fontSize="120" fontWeight="700" fill={C.red}>{n(th.new_domains)}</text>
    <text x={PAD} y={360} fontFamily={SANS} fontSize="36" fontWeight="700" fill={C.text}>
      {t("new threat domains this month", "bu ay yeni tehdit alan adı")}</text>
    <text x={PAD} y={410} fontFamily={SANS} fontSize="28" fill={C.muted}>
      {t(`${n(th.own_domains)} confirmed by our reviewers and our page detector`, `${n(th.own_domains)} tanesini inceleyicilerimiz ve sayfa dedektörümüz doğruladı`)}</text>
    <text x={PAD} y={500} fontFamily={SANS} fontSize="26" fontWeight="700" fill={C.muted} letterSpacing="2">
      {t(`DOMAINS BY TYPE (${n(f.labels)} LABELS)`, `TÜRE GÖRE ALAN ADI (${n(f.labels)} ETİKET)`)}</text>
    <Bars y={560} rowH={72} items={types} label={c.cat} value={(it) => n(it.count)} />
    <text x={PAD} y={flowY} fontFamily={SANS} fontSize="26" fontWeight="700" fill={C.muted} letterSpacing="2">{t("CANDIDATES FOR A PERSON", "İNSAN KARARI BEKLEYEN ADAYLAR")}</text>
    <text x={PAD} y={flowY + 52} fontFamily={SANS} fontSize="30" fill={C.text}>
      {t(`${n(f.candidates)} opened → ${n(f.decided)} decided → ${n(f.confirmed)} confirmed`,
         `${n(f.candidates)} açıldı → ${n(f.decided)} karara bağlandı → ${n(f.confirmed)} doğrulandı`)}
      {f.decided > 0 && <tspan fill={C.muted}>{`  (${c.pct(f.confirmed, f.decided)})`}</tspan>}</text>
    {pills.length > 0 && <>
      <text x={PAD} y={brandsY} fontFamily={SANS} fontSize="26" fontWeight="700" fill={C.muted} letterSpacing="2">
        {t("MOST IMITATED BRANDS", "EN ÇOK TAKLİT EDİLEN MARKALAR")}</text>
      {pills.map((p) => (
        <g key={p.key}>
          <rect x={p.x} y={p.y} width={p.w} height={52} rx="26" fill={C.redSoft} />
          <text x={p.x + 24} y={p.y + 35} fontFamily={SANS} fontSize="28" fill={C.text}>{p.text}</text>
        </g>
      ))}
    </>}
    {th.suffixes.length > 0 &&
      <text x={PAD} y={suffixY} fontFamily={SANS} fontSize="28" fill={C.muted}>
        {t("Most used endings: ", "En çok kullanılan uzantılar: ")}
        <tspan fill={C.text} fontWeight="700">{th.suffixes.filter((s) => s.key !== "other").slice(0, 5).map((s) => s.key).join("  ·  ")}</tspan></text>}
  </>);
}

function Funnel({ d, c }: { d: Data; c: Ctx }) {
  const { t, n, pct } = c;
  const f = d.funnel;
  const stages: [string, number][] = [
    [t("known domains", "bilinen alan adı"), f.known],
    [t("successfully scanned this month", "bu ay başarıyla taranan"), f.scanned],
    [t("with a published category", "kategorisi yayında"), f.published],
    [t("checked by a person", "insan tarafından doğrulanan"), f.verified],
  ];
  const max = Math.max(1, f.known);
  const width = W - 2 * PAD;
  return (<>
    <Title lines={t("From discovery,to verification", "Keşiften,doğrulamaya").split(",")}
           sub={t(`${n(f.new_known)} domains discovered this month`, `Bu ay ${n(f.new_known)} yeni alan adı keşfedildi`)} />
    {stages.map(([label, value], i) => {
      const top = 520 + i * 130;
      const w = Math.max(8, (value / max) * width);
      return (
        <g key={label}>
          <text x={PAD} y={top} fontFamily={SANS} fontSize="30" fill={C.text}>{label}</text>
          <text x={W - PAD} y={top} textAnchor="end" fontFamily={SERIF} fontSize="40" fontWeight="700" fill={C.text}>{n(value)}</text>
          <rect x={PAD} y={top + 18} width={width} height={34} rx="4" fill={C.beige} />
          <rect x={PAD} y={top + 18} width={w} height={34} rx="4" fill={C.red} />
          {i > 0 && <text x={PAD} y={top + 84} fontFamily={SANS} fontSize="24" fill={C.muted}>
            {t(`${pct(value, f.known)} of known domains`, `bilinen alan adlarına oranı ${pct(value, f.known)}`)}</text>}
        </g>
      );
    })}
    <text x={PAD} y={1100} fontFamily={SANS} fontSize="28" fill={C.muted}>
      {t(`Category coverage ${pct(f.published, f.known)} · checked by people ${pct(f.verified, f.published)} of published`,
         `Kategori kapsamı ${pct(f.published, f.known)} · insan doğrulaması oranı ${pct(f.verified, f.published)}`)}</text>
  </>);
}

function AiQuality({ d, c }: { d: Data; c: Ctx }) {
  const { t, n, pct } = c;
  const a = d.ai;
  const monthly = a.sample_n >= 20;
  const sn = monthly ? a.sample_n : a.all_sample_n;
  const sk = monthly ? a.sample_kept : a.all_sample_kept;
  const ci = (lo: number | null, hi: number | null) => (lo == null || hi == null ? ""
    : ` · ${t("95 % interval", "güven aralığı")} ${pct(Math.round(lo * 1000), 1000)}–${pct(Math.round(hi * 1000), 1000)}`);
  const w = (W - 2 * PAD - 32) / 2, h = 330;
  const card = (x: number, value: string, lines: string[], foot: string) => (
    <g>
      <rect x={x} y={500} width={w} height={h} rx="24" fill={C.panel} stroke={C.line} strokeWidth="2" />
      <text x={x + 36} y={612} fontFamily={SERIF} fontSize="88" fontWeight="700" fill={C.red}>{value}</text>
      {lines.map((l, i) => <text key={i} x={x + 36} y={672 + i * 38} fontFamily={SANS} fontSize="30" fill={C.text}>{l}</text>)}
      <text x={x + 36} y={800} fontFamily={SANS} fontSize="22" fill={C.muted}>{foot}</text>
    </g>
  );
  return (<>
    <Title lines={t("How right is,the AI?", "Yapay zekâ,ne kadar doğru?").split(",")}
           sub={t("Measured against people, not against the model", "Modelin kendisiyle değil, insan kararıyla ölçülür")} />
    {card(PAD, sn ? pct(sk, sn) : "–", t("of published answers,kept by a reviewer", "yayımlanmış cevabı,insan değiştirmedi").split(","),
          `n=${n(sn)}${monthly ? ci(a.sample_low, a.sample_high) : t(" · all months", " · tüm aylar")}`)}
    {card(PAD + w + 32, a.hard_n ? pct(a.hard_kept, a.hard_n) : "–", t("of hard cases,right first time", "zor vakada,ilk cevap doğru").split(","),
          `n=${n(a.hard_n)} · ${t("unsure or disputed answers", "emin olunmayan cevaplar")}`)}
    <text x={PAD} y={920} fontFamily={SANS} fontSize="26" fontWeight="700" fill={C.muted} letterSpacing="2">{t("VALID ANSWERS (FORMAT, NOT CORRECTNESS)", "GEÇERLİ YANIT (BİÇİM, DOĞRULUK DEĞİL)")}</text>
    <rect x={PAD} y={945} width={W - 2 * PAD} height={30} rx="4" fill={C.beige} />
    <rect x={PAD} y={945} width={Math.max(8, (a.valid / Math.max(1, a.answers)) * (W - 2 * PAD))} height={30} rx="4" fill={C.red} />
    <text x={PAD} y={1020} fontFamily={SANS} fontSize="28" fill={C.text}>
      {t(`${pct(a.valid, a.answers)} of ${n(a.answers)} AI answers this month`, `Bu ayki ${n(a.answers)} yapay zekâ cevabında oran ${pct(a.valid, a.answers)}`)}</text>
    <text x={PAD} y={1070} fontFamily={SANS} fontSize="24" fill={C.muted}>{t("Models", "Modeller")}: {a.models.join(", ") || "–"}</text>
  </>);
}

function Crawler({ d, c }: { d: Data; c: Ctx }) {
  const { t, n, pct } = c;
  const k = d.crawler;
  const technical = k.succeeded + k.failed;
  const w = (W - 2 * PAD - 64) / 3;
  const stats: [string, string][] = [[n(k.unique_succeeded), t("domains scanned", "alan adı tarandı")],
                                     [technical ? pct(k.succeeded, technical) : "–", t("technical success", "teknik başarı")],
                                     [n(k.blocked), t("refused by policy", "politika engeli")]];
  const total = Math.max(1, k.jobs);
  const parts: [string, number, string][] = [[t("succeeded", "başarılı"), k.succeeded, C.red],
                                            [t("technical error", "teknik hata"), k.failed, "#8A6F62"],
                                            [t("robots / policy", "robots / politika"), k.blocked, "#D9C3A5"]];
  let x = PAD;
  const chartTop = 900, chartH = 150, chartW = W - 2 * PAD;
  const peak = Math.max(1, ...k.daily.map((p) => p.count));
  const [y, m] = d.month.split("-").map(Number);
  const days = new Date(y, m, 0).getDate();
  const pts = k.daily.map((p) => [PAD + ((Number(p.key.slice(8, 10)) - 1) / Math.max(1, days - 1)) * chartW,
                                  chartTop + chartH - (p.count / peak) * chartH] as const);
  const line = pts.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)} ${py.toFixed(1)}`).join(" ");
  return (<>
    <Title lines={t("Crawling,capacity", "Tarama,kapasitesi").split(",")} />
    {stats.map(([v, l], i) => (
      <g key={l}>
        <text x={PAD + i * (w + 32)} y={470} fontFamily={SERIF} fontSize="64" fontWeight="700" fill={C.red}>{v}</text>
        <text x={PAD + i * (w + 32)} y={512} fontFamily={SANS} fontSize="28" fill={C.muted}>{l}</text>
      </g>
    ))}
    <text x={PAD} y={600} fontFamily={SANS} fontSize="26" fontWeight="700" fill={C.muted} letterSpacing="2">
      {t(`${n(k.jobs)} SCAN JOBS`, `${n(k.jobs)} TARAMA İŞİ`)}</text>
    {parts.map(([label, value, color]) => {
      const pw = (value / total) * (W - 2 * PAD);
      const el = <rect key={label} x={x} y={620} width={Math.max(0, pw - 2)} height={40} rx="4" fill={color} />;
      x += pw;
      return el;
    })}
    {parts.map(([label, value, color], i) => (
      <g key={`l-${label}`}>
        <rect x={PAD + i * 310} y={690} width={22} height={22} rx="4" fill={color} />
        <text x={PAD + i * 310 + 34} y={709} fontFamily={SANS} fontSize="24" fill={C.text}>{label} {n(value)}</text>
      </g>
    ))}
    <text x={PAD} y={780} fontFamily={SANS} fontSize="26" fill={C.muted}>
      {t(`Recovered by a retry: ${n(k.recovered)} of ${n(k.retried)}`, `Yeniden denemeyle kurtarılan: ${n(k.retried)} işten ${n(k.recovered)}`)}</text>
    {pts.length > 1 && <>
      <text x={PAD} y={chartTop - 30} fontFamily={SANS} fontSize="26" fontWeight="700" fill={C.muted} letterSpacing="2">
        {t("DOMAINS SCANNED PER DAY", "GÜNLÜK TARANAN ALAN ADI")}</text>
      <line x1={PAD} x2={W - PAD} y1={chartTop + chartH} y2={chartTop + chartH} stroke={C.line} strokeWidth="2" />
      <path d={`${line} L${pts[pts.length - 1][0].toFixed(1)} ${chartTop + chartH} L${pts[0][0].toFixed(1)} ${chartTop + chartH} Z`} fill={C.redSoft} />
      <path d={line} fill="none" stroke={C.red} strokeWidth="3" strokeLinejoin="round" />
    </>}
  </>);
}

function Usage({ d, c }: { d: Data; c: Ctx }) {
  const { t, n, pct } = c;
  const u = d.usage;
  const w = (W - 2 * PAD - 32) / 2;
  const formats = u.formats.slice(0, 6);
  return (<>
    <Title lines={t("Where is the,data used?", "Veri nerede,kullanılıyor?").split(",")} />
    {([[n(u.feed_downloads), t("block-list download requests", "engelleme listesi indirme isteği")],
       [n(u.lookups), t("site and API lookups", "site ve API sorgusu")]] as [string, string][]).map(([v, l], i) => (
      <g key={l}>
        <text x={PAD + i * (w + 32)} y={470} fontFamily={SERIF} fontSize="64" fontWeight="700" fill={C.red}>{v}</text>
        <text x={PAD + i * (w + 32)} y={512} fontFamily={SANS} fontSize="28" fill={C.muted}>{l}</text>
      </g>
    ))}
    <text x={PAD} y={600} fontFamily={SANS} fontSize="26" fontWeight="700" fill={C.muted} letterSpacing="2">{t("DOWNLOADS BY FORMAT", "FORMATA GÖRE İNDİRME")}</text>
    {formats.length === 0 ? <Empty y={660} text={t("No downloads this month.", "Bu ay indirme yok.")} /> :
      <Bars y={660} rowH={74} items={formats} label={(k) => (k === "other" ? t("Other", "Diğer") : `.${k}`)}
            value={(it) => `${pct(it.count, u.feed_downloads)}  ·  ${n(it.count)}`} />}
    {u.lists.length > 0 && <text x={PAD} y={1120} fontFamily={SANS} fontSize="26" fill={C.muted}>
      {t("Most downloaded: ", "En çok indirilen: ")}
      <tspan fill={C.text} fontWeight="700">{u.lists.filter((l) => l.key !== "other").slice(0, 3).map((l) => l.key).join("  ·  ")}</tspan></text>}
  </>);
}

export function makeCtx(lang: Locale, month: string, site: string, cats: Cat[]): Ctx {
  const t = makeT(lang);
  const names = Object.fromEntries(cats.map((x) => [x.id, x.name]));
  return {
    lang, t, month, site,
    n: (v: number) => v.toLocaleString(intl(lang)),
    pct: (part: number, whole: number) => {
      const v = whole ? Math.round((part / whole) * 100) : 0;
      return lang === "tr" ? `%${v}` : `${v}%`;
    },
    cat: (id: string) => (id === "other" ? t("Other", "Diğer") : categoryName(lang, id, names[id])),
  };
}

function period(d: Data, c: Ctx): string {
  const [y, m] = d.month.split("-").map(Number);
  const days = new Date(y, m, 0).getDate();
  const made = new Date(d.generated_at).toLocaleString(intl(c.lang), { dateStyle: "short", timeStyle: "short", timeZone: "Europe/Istanbul" });
  return c.t(`Period 1–${days} ${monthName(d.month, c.lang)}${d.complete ? "" : " (in progress)"} · made ${made} · source: Cat-Egory database`,
             `Dönem 1–${days} ${monthName(d.month, c.lang)}${d.complete ? "" : " (sürüyor)"} · üretildi ${made} · kaynak: Cat-Egory veritabanı`);
}

type Note = (d: Data, c: Ctx) => string;
const NOTES: Record<string, Note> = {
  cover: (d, c) => c.t("Domains counted once each (registrable domain). Threat: a domain first recorded as a threat this month.",
                       "Alan adları birer kez sayılır (kayıtlı alan adı). Tehdit: bu ay ilk kez tehdit olarak kaydedilen alan adı."),
  funnel: (d, c) => c.t("Known: active domains seen by the end of the month. Scanned: at least one successful whole-site scan in the month. Published and checked: status when made.",
                        "Bilinen: ay sonuna kadar görülen etkin alan adı. Taranan: ay içinde en az bir başarılı tam site taraması. Yayında ve doğrulanan: üretim anındaki durum."),
  tr: (d, c) => c.t("Main category of published .tr domains, status when made.", "Yayımlanmış .tr alan adlarının ana kategorisi, üretim anındaki durum."),
  threats: (d, c) => c.t("Domains counted once each; one domain can carry several threat types (labels). Candidates: look-alikes, list hits and model claims a person decides on.",
                         "Alan adları birer kez sayılır; bir alan adı birden çok tehdit türü (etiket) taşıyabilir. Aday: insanın karar verdiği taklit, liste ve model işaretleri."),
  ai: (d, c) => c.t("Kept: published answers sampled at random and left unchanged by a reviewer (Wilson 95 %). Valid: the answer's format only, not its correctness.",
                    "Kabul: yayımlanmış cevaplardan rastgele örneklenip insanın değiştirmediği oran (Wilson %95). Geçerli: yalnızca cevabın biçimi, doğruluğu değil."),
  crawler: (d, c) => c.t("Whole-site scan jobs finished in the month. Technical success: succeeded ÷ (succeeded + technical errors); robots.txt and policy refusals are counted apart.",
                         "Ay içinde biten tam site tarama işleri. Teknik başarı: başarılı ÷ (başarılı + teknik hata); robots.txt ve politika engelleri ayrı sayılır."),
  usage: (d, c) => c.t("Download and lookup counts are requests, not installations or users; nothing records who asked.",
                       "İndirme ve sorgu sayıları istektir, kurulum ya da kullanıcı sayısı değildir; kimin sorduğu kaydedilmez."),
};

export const SLIDES = [
  { id: "cover", en: "Cover", tr: "Kapak", Body: Cover },
  { id: "funnel", en: "Discovery", tr: "Keşif hunisi", Body: Funnel },
  { id: "tr", en: ".tr categories", tr: ".tr kategorileri", Body: TrCategories },
  { id: "threats", en: "Threats", tr: "Tehditler", Body: Threats },
  { id: "ai", en: "AI quality", tr: "YZ kalitesi", Body: AiQuality },
  { id: "crawler", en: "Crawler", tr: "Tarama", Body: Crawler },
  { id: "usage", en: "Feeds and API", tr: "Liste ve API", Body: Usage },
] as const;

/** One card as a standalone SVG (also rendered by scripts outside the app to check the layout). */
export function Card({ ref, d, c, id, Body, label, page, pages }: { ref?: React.Ref<SVGSVGElement>; d: Data; c: Ctx; id: string;
  Body: (p: { d: Data; c: Ctx }) => React.ReactNode; label: string; page: number; pages: number }) {
  return (
    <svg ref={ref} xmlns="http://www.w3.org/2000/svg" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={label}>
      <Frame c={c} page={page} pages={pages} id={id}
             note={`${(NOTES[id] ?? (() => ""))(d, c)} ${period(d, c)}`.trim()}><Body d={d} c={c} /></Frame>
    </svg>
  );
}

async function png(svg: SVGSVGElement): Promise<Blob> {
  const xml = new XMLSerializer().serializeToString(svg);
  const img = new Image();
  img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(xml)}`;
  await img.decode();
  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  canvas.getContext("2d")!.drawImage(img, 0, 0, W, H);
  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("png"))), "image/png"));
}

function save(blob: Blob, name: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function postText(d: Data, c: Ctx): string {
  const { t, n, pct } = c;
  const top = d.tr_categories.find((x) => x.key !== "other");
  const brand = d.threats.brands[0];
  const a = d.ai;
  const lines = [
    t(`Cat-Egory, ${monthName(d.month, c.lang)} in numbers`, `Cat-Egory ${monthName(d.month, c.lang)} bülteni`),
    "",
    t(`• ${n(d.funnel.known)} known domains, ${n(d.funnel.new_known)} discovered this month; ${n(d.totals.published)} have a published category, ${n(d.totals.new_published)} of them new.`,
      `• ${n(d.funnel.known)} bilinen alan adı, bu ay ${n(d.funnel.new_known)} yeni keşif; ${n(d.totals.published)} sitenin kategorisi yayında, ${n(d.totals.new_published)} tanesi bu ay eklendi.`),
    ...(top ? [t(`• The largest category among .tr websites: ${c.cat(top.key)} (${pct(top.count, d.totals.tr_published)}).`,
                 `• .tr siteleri arasında en büyük kategori: ${c.cat(top.key)} (${pct(top.count, d.totals.tr_published)}).`)] : []),
    ...(d.threats.new_domains ? [t(`• ${n(d.threats.new_domains)} new threat domains were recorded${brand ? `; the most imitated brand: ${brand.key}` : ""}.`,
                                   `• Bu ay ${n(d.threats.new_domains)} alan adı tehdit olarak kaydedildi${brand ? `; en çok taklit edilen marka: ${brand.key}` : ""}.`)] : []),
    ...(a.all_sample_n ? [t(`• Of ${n(a.all_sample_n)} published AI answers checked by people at random, ${pct(a.all_sample_kept, a.all_sample_n)} were kept unchanged.`,
                            `• İnsan denetimine alınan, rastgele seçilmiş ${n(a.all_sample_n)} yayımlanmış cevapta değiştirilmeden kabul oranı ${pct(a.all_sample_kept, a.all_sample_n)}.`)] : []),
    ...(d.crawler.unique_succeeded ? [t(`• ${n(d.crawler.unique_succeeded)} domains were scanned successfully this month.`,
                                        `• Bu ay ${n(d.crawler.unique_succeeded)} alan adı başarıyla tarandı.`)] : []),
    "",
    t(`The data is open and free (CC BY 4.0), with DNS block lists for firewalls: ${c.site}`,
      `Veriler açık ve ücretsiz (CC BY 4.0); güvenlik duvarları için DNS engelleme listeleri de var: ${c.site}`),
    "",
    t("#cybersecurity #phishing #opendata #DNS #Türkiye", "#siberguvenlik #oltalama #phishing #açıkveri #DNS"),
  ];
  return lines.join("\n");
}

function View({ api, site }: { api: string; site: string }) {
  const ui = useT();
  const uiLocale = useLocale();
  const choices = useMemo(() => months(), []);
  const [month, setMonth] = useState(choices[1]);
  const [lang, setLang] = useState<Locale>("tr");
  const [data, setData] = useState<Data | null>(null);
  const [cats, setCats] = useState<Cat[]>([]);
  const [msg, setMsg] = useState("");
  const [skip, setSkip] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const refs = useRef<Record<string, SVGSVGElement | null>>({});
  const text = useRef<HTMLTextAreaElement>(null);

  const load = useCallback(async () => {
    setMsg("");
    const [r, c] = await Promise.all([call<Data>(api, `/api/v1/admin/infographic?month=${month}`),
                                      call<{ items: Cat[] }>(api, "/api/v1/categories", { auth: false })]);
    if (r.ok) setData(r.data); else { setData(null); setMsg(`${r.message} (${r.code})`); }
    if (c.ok) setCats(c.data.items);
  }, [api, month]);
  useEffect(() => { void load(); }, [load]);

  const ctx = useMemo(() => makeCtx(lang, month, site, cats), [cats, lang, month, site]);

  const shown = SLIDES.filter((s) => !skip.has(s.id));

  async function download(ids: string[]) {
    setBusy(true);
    try {
      for (const [i, id] of ids.entries()) {
        const svg = refs.current[id];
        if (!svg) continue;
        const page = shown.findIndex((s) => s.id === id) + 1;
        save(await png(svg), `cat-egory-${month}-${page}-${id}.png`);
        if (i < ids.length - 1) await new Promise((r) => setTimeout(r, 300));  // browsers drop rapid downloads
      }
    } catch { setMsg(ui("The image could not be made in this browser.", "Bu tarayıcıda görsel oluşturulamadı.")); }
    setBusy(false);
  }

  async function copy() {
    const value = text.current?.value ?? "";
    try { await navigator.clipboard.writeText(value); setMsg(ui("Post text copied.", "Gönderi metni kopyalandı.")); }
    catch {  // no clipboard API on plain http (the admin UI on the home network): select and copy the old way
      text.current?.select();
      setMsg(document.execCommand("copy") ? ui("Post text copied.", "Gönderi metni kopyalandı.")
                                          : ui("Select the text and copy it.", "Metni seçip kopyalayın."));
    }
  }

  return (
    <div className="stack">
      <div className="row">
        <label>{ui("Month", "Ay")}{" "}
          <select value={month} onChange={(e) => setMonth(e.target.value)}>
            {choices.map((m, i) => <option key={m} value={m}>{monthName(m, uiLocale)}{i === 0 ? ui(" (in progress)", " (sürüyor)") : ""}</option>)}
          </select></label>
        <label>{ui("Card language", "Kart dili")}{" "}
          <select value={lang} onChange={(e) => setLang(e.target.value as Locale)}>
            <option value="tr">Türkçe</option><option value="en">English</option>
          </select></label>
        <button disabled={!data || busy || shown.length === 0} onClick={() => void download(shown.map((s) => s.id))}>
          {ui(`Download All (${shown.length} PNG)`, `Tümünü İndir (${shown.length} PNG)`)}</button>
      </div>
      {data && !data.complete && <div className="notice">{ui("This month is not over yet: the numbers are partial.", "Bu ay henüz bitmedi: sayılar eksik.")}</div>}
      {msg && <div className="notice" role="status">{msg}</div>}
      {data && <>
        <div className="infographic-grid">
          {SLIDES.map(({ id, en, tr, Body }) => {
            const page = shown.findIndex((s) => s.id === id) + 1;
            return (
              <figure key={id} className={`infographic-card ${skip.has(id) ? "skipped" : ""}`}>
                <Card ref={(el) => { refs.current[id] = el; }} d={data} c={ctx} id={id} Body={Body} label={ui(en, tr)}
                      page={page || 1} pages={shown.length || 1} />
                <figcaption className="row">
                  <label><input type="checkbox" checked={!skip.has(id)} onChange={(e) => setSkip((s) => {
                    const next = new Set(s); if (e.target.checked) next.delete(id); else next.add(id); return next; })} /> {ui(en, tr)}</label>
                  <button className="secondary" disabled={busy || skip.has(id)} onClick={() => void download([id])}>PNG</button>
                </figcaption>
              </figure>
            );
          })}
        </div>
        <h2>{ui("Post text", "Gönderi metni")}</h2>
        <textarea ref={text} className="infographic-text" readOnly value={postText(data, ctx)} rows={12} />
        <div className="row"><button className="secondary" onClick={() => void copy()}>{ui("Copy", "Kopyala")}</button></div>
      </>}
    </div>
  );
}

export default function Infographic({ api, site }: { api: string; site: string }) {
  return <KeyGate api={api}>{() => <View api={api} site={site} />}</KeyGate>;
}
