"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { call } from "@/lib/client";
import KeyGate from "./KeyGate";
import { useLocale, useT } from "./LocaleProvider";
import { categoryName, intl, type Locale, type T } from "@/lib/i18n";

type Threshold = { threshold: number; answered: number; correct: number };
type Group = { pages: number; correct: number; thresholds: Threshold[] };
type Model = { version: string; trained_at: string; current: boolean; train_examples: number; test_examples: number;
  ai_labels: boolean | null; seconds: number | null; peak_memory_mb: number | null;
  class_balance: number | null; macro_recall: number | null; human: Group | null; ai: Group | null };
type Day = { day: string; models: string[]; guesses: number; compared: number; agree: number; alone: number;
  alone_agree: number };
type Data = { shadow: boolean; alone_threshold: number; auto_approve: number; days: number; since: string;
  current: Model | null; categories: { category: string; pages: number; guessed: number; correct: number }[];
  too_small: string[]; models: Model[]; compared: number; thresholds: Threshold[]; daily: Day[];
  people: { decided: number; agree: number } };

const ratio = (part: number, whole: number): number | null => (whole ? part / whole : null);

function useFormat() {
  const locale = useLocale();
  return useMemo(() => {
    const pct = new Intl.NumberFormat(intl(locale), { style: "percent", maximumFractionDigits: 0 });
    const num = new Intl.NumberFormat(intl(locale));
    const dec = new Intl.NumberFormat(intl(locale), { minimumFractionDigits: 1, maximumFractionDigits: 2 });
    return {
      pct: (v: number | null) => (v === null ? "–" : pct.format(v)),
      num: (v: number) => num.format(v),
      dec: (v: number) => dec.format(v),
      day: (iso: string) => new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString(intl(locale), { day: "numeric", month: "short" }),
      when: (iso: string) => new Date(iso).toLocaleString(intl(locale), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }),
    };
  }, [locale]);
}

/** Every day of the period, oldest first, so the time axis has no jumps; days without guesses stay empty. */
function allDays(since: string, days: number, daily: Day[]): Day[] {
  const byDay = new Map(daily.map((d) => [d.day, d]));
  const start = new Date(`${since}T12:00:00Z`);
  return Array.from({ length: days }, (_, i) => {
    const day = new Date(start.getTime() + i * 86_400_000).toISOString().slice(0, 10);
    return byDay.get(day) ?? { day, models: [], guesses: 0, compared: 0, agree: 0, alone: 0, alone_agree: 0 };
  });
}

// --------------------------------------------------------------------------- charts (plain SVG, no library)

type Series = { name: string; tone: "a" | "b"; values: (number | null)[] };
const W = 720, PAD = { l: 44, r: 16, t: 14, b: 28 };

function ticks(max: number): number[] {
  if (max <= 0) return [0];
  const step = 10 ** Math.floor(Math.log10(max));
  const nice = [1, 2, 5, 10].map((m) => m * step).find((s) => max / s <= 5) ?? step * 10;
  return Array.from({ length: Math.floor(max / nice) + 1 }, (_, i) => i * nice);
}

/** Which x positions get a label: about eight, always the last, never two on top of each other. */
function labelled(n: number): Set<number> {
  const every = Math.max(1, Math.ceil(n / 8));
  const at = Array.from({ length: n }, (_, i) => i).filter((i) => i % every === 0);
  if (at[at.length - 1] !== n - 1) {
    if (n - 1 - at[at.length - 1] < every * 0.6) at.pop();
    at.push(n - 1);
  }
  return new Set(at);
}

function Legend({ series }: { series: Series[] }) {
  return (
    <div className="chart-legend">
      {series.map((s) => <span key={s.name}><i className={`key key-${s.tone}`} aria-hidden="true" />{s.name}</span>)}
    </div>
  );
}

/** Lines on a 0–100 % scale. A gap in the data is a gap in the line; an isolated point is a dot. */
function LineChart({ labels, series, tip, marks = [], height = 230, label }: {
  labels: string[]; series: Series[]; tip: (i: number) => React.ReactNode; marks?: { at: number; text: string }[];
  height?: number; label: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const f = useFormat();
  const n = labels.length;
  const iw = W - PAD.l - PAD.r, ih = height - PAD.t - PAD.b;
  const x = (i: number) => PAD.l + (n === 1 ? iw / 2 : (i * iw) / (n - 1));
  const y = (v: number) => PAD.t + ih - v * ih;
  const shown = labelled(n);
  const band = n === 1 ? iw : iw / (n - 1);
  return (
    <div className="chart" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={label}>
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <g key={v}><line className="grid" x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} />
            <text className="axis" x={PAD.l - 8} y={y(v) + 4} textAnchor="end">{f.pct(v)}</text></g>
        ))}
        {labels.map((l, i) => shown.has(i) && (
          <text key={i} className="axis" x={x(i)} y={height - 8} textAnchor="middle">{l}</text>
        ))}
        {marks.map((m) => (
          <g key={m.at}><line className="mark-line" x1={x(m.at)} x2={x(m.at)} y1={PAD.t} y2={PAD.t + ih} />
            <text className="axis" x={x(m.at) + 4} y={PAD.t + 10}>{m.text}</text></g>
        ))}
        {hover !== null && <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={PAD.t + ih} />}
        {series.map((s) => {
          const runs: [number, number][][] = [];
          s.values.forEach((v, i) => {
            if (v === null) return;
            if (i === 0 || s.values[i - 1] === null) runs.push([]);
            runs[runs.length - 1].push([x(i), y(v)]);
          });
          return (
            <g key={s.name} className={`series-${s.tone}`}>
              {runs.filter((r) => r.length > 1).map((r, k) => (
                <polyline key={k} className="line" points={r.map(([a, b]) => `${a},${b}`).join(" ")} />))}
              {runs.map((r, k) => (r.length === 1 || n <= 16 ? r : [r[r.length - 1]]).map(([a, b]) => (
                <circle key={`${k}-${a}`} className="dot" cx={a} cy={b} r={4} />)))}
              {hover !== null && s.values[hover] !== null && (
                <circle className="dot" cx={x(hover)} cy={y(s.values[hover] as number)} r={5} />)}
            </g>
          );
        })}
        {labels.map((_, i) => (
          <rect key={i} className="hit" x={x(i) - band / 2} y={PAD.t} width={band} height={ih}
                onMouseEnter={() => setHover(i)} />
        ))}
      </svg>
      {hover !== null && (
        <div className={`chart-tip ${x(hover) > W / 2 ? "left" : "right"}`} style={{ left: `${(x(hover) / W) * 100}%` }}>{tip(hover)}</div>
      )}
    </div>
  );
}

/** One series of counts as columns from a zero baseline. */
function Columns({ labels, values, tip, label, height = 150 }: {
  labels: string[]; values: number[]; tip: (i: number) => React.ReactNode; label: string; height?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const f = useFormat();
  const n = labels.length;
  const iw = W - PAD.l - PAD.r, ih = height - PAD.t - PAD.b;
  const max = Math.max(1, ...values);
  const scale = ticks(max);
  const top = Math.max(max, scale[scale.length - 1]);
  const slot = iw / n, bw = Math.min(24, Math.max(2, slot - 2));
  const cx = (i: number) => PAD.l + slot * i + slot / 2;
  const y = (v: number) => PAD.t + ih - (v / top) * ih;
  const shown = labelled(n);
  return (
    <div className="chart" onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={label}>
        {scale.map((v) => (
          <g key={v}><line className="grid" x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} />
            <text className="axis" x={PAD.l - 8} y={y(v) + 4} textAnchor="end">{f.num(v)}</text></g>
        ))}
        {labels.map((l, i) => shown.has(i) && (
          <text key={i} className="axis" x={cx(i)} y={height - 8} textAnchor="middle">{l}</text>
        ))}
        {values.map((v, i) => {
          if (!v) return null;
          const h = PAD.t + ih - y(v), r = Math.min(4, h, bw / 2);
          const x0 = cx(i) - bw / 2, x1 = cx(i) + bw / 2, base = PAD.t + ih;
          return (
            <path key={i} className={`column${hover === i ? " on" : ""}`}
                  d={`M${x0},${base}V${base - h + r}Q${x0},${base - h} ${x0 + r},${base - h}H${x1 - r}Q${x1},${base - h} ${x1},${base - h + r}V${base}Z`} />
          );
        })}
        {labels.map((_, i) => (
          <rect key={i} className="hit" x={PAD.l + slot * i} y={PAD.t} width={slot} height={ih}
                onMouseEnter={() => setHover(i)} />
        ))}
      </svg>
      {hover !== null && <div className={`chart-tip ${cx(hover) > W / 2 ? "left" : "right"}`} style={{ left: `${(cx(hover) / W) * 100}%` }}>{tip(hover)}</div>}
    </div>
  );
}

// --------------------------------------------------------------------------- page

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return <div className="panel tile"><div className="label">{label}</div><div className="value">{value}</div>
    <div className="muted small">{sub}</div></div>;
}

function HeldOut({ t, title, group }: { t: T; title: string; group: Group | null }) {
  const f = useFormat();
  if (!group) return null;
  return (<>
    <h3>{title}: {f.num(group.correct)}/{f.num(group.pages)} ({f.pct(ratio(group.correct, group.pages))})</h3>
    <div className="panel"><table>
      <thead><tr><th>{t("Model confidence ≥", "Model güveni ≥")}</th><th className="num">{t("Answers alone", "Kendi başına cevaplar")}</th>
        <th className="num">{t("Right", "Doğru")}</th></tr></thead>
      <tbody>{group.thresholds.map((r) => (
        <tr key={r.threshold}><td>{f.dec(r.threshold)}</td>
          <td className="num">{f.num(r.answered)} ({f.pct(ratio(r.answered, group.pages))})</td>
          <td className="num">{f.num(r.correct)}/{f.num(r.answered)} ({f.pct(ratio(r.correct, r.answered))})</td></tr>
      ))}</tbody>
    </table></div>
  </>);
}

function View({ api }: { api: string }) {
  const t = useT();
  const locale = useLocale() as Locale;
  const f = useFormat();
  const [days, setDays] = useState(30);
  const [data, setData] = useState<Data | null>(null);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    const r = await call<Data>(api, `/api/v1/admin/yasmama?days=${days}`);
    if (r.ok) { setData(r.data); setMsg(""); } else setMsg(`${r.message} (${r.code})`);
  }, [api, days]);
  useEffect(() => { void load(); }, [load]);

  if (!data) return <p className="muted">{msg || t("Loading…", "Yükleniyor…")}</p>;
  const alone = f.dec(data.alone_threshold);
  const series = allDays(data.since, data.days, data.daily);
  const labels = series.map((d) => f.day(d.day));
  const totals = series.reduce((a, d) => ({ agree: a.agree + d.agree, alone: a.alone + d.alone,
    aloneAgree: a.aloneAgree + d.alone_agree }), { agree: 0, alone: 0, aloneAgree: 0 });
  const changes = series.flatMap((d, i) => {
    const before = series.slice(0, i).reverse().find((p) => p.models.length);
    return before && d.models.length && d.models[d.models.length - 1] !== before.models[before.models.length - 1]
      ? [{ at: i, text: t("new model", "yeni model") }] : [];
  });
  const agreeName = t("Agrees with EVREN", "EVREN'le aynı");
  const aloneName = t(`Agrees when ≥ ${alone} sure`, `≥ ${alone} eminken EVREN'le aynı`);
  const daySeries: Series[] = [
    { name: agreeName, tone: "a", values: series.map((d) => ratio(d.agree, d.compared)) },
    { name: aloneName, tone: "b", values: series.map((d) => ratio(d.alone_agree, d.alone)) },
  ];
  const peopleName = t("Pages people labelled", "İnsanların etiketlediği sayfalar");
  const aiName = t("Pages EVREN labelled", "EVREN'in etiketlediği sayfalar");
  // every category counts the same: shows whether the small ones are learnt too (local_class_balance)
  const macroName = t("Category average", "Kategori ortalaması");
  const modelSeries: Series[] = [
    { name: peopleName, tone: "a", values: data.models.map((m) => (m.human ? ratio(m.human.correct, m.human.pages) : null)) },
    { name: aiName, tone: "b", values: data.models.map((m) => (m.ai ? ratio(m.ai.correct, m.ai.pages) : null)) },
  ];
  const cur = data.current;

  return (
    <div className="stack">
      <div className="row">
        <select value={days} aria-label={t("Period", "Dönem")} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 30, 90].map((d) => <option key={d} value={d}>{t(`Last ${d} days`, `Son ${d} gün`)}</option>)}
        </select>
        <span className="muted">{t(`Since ${data.since} (Türkiye time)`, `${data.since} tarihinden beri (Türkiye saati)`)}</span>
        {msg && <span className="muted">{msg}</span>}
      </div>
      {!data.shadow && <div className="notice">{t(
        "Shadow mode is off (CATEGORY_LOCAL_SHADOW): no new guesses and no weekly training. Older numbers stay visible.",
        "Gölge mod kapalı (CATEGORY_LOCAL_SHADOW): yeni tahmin ve haftalık eğitim yok. Eski sayılar görünmeye devam eder.")}</div>}
      {!cur && <div className="notice">{t("No model trained yet.", "Henüz eğitilmiş model yok.")}</div>}

      <div className="tiles">
        <Tile label={t("Agrees with EVREN", "EVREN'le uyum")} value={f.pct(ratio(totals.agree, data.compared))}
              sub={t(`${f.num(totals.agree)} of ${f.num(data.compared)} confident answers`, `${f.num(totals.agree)}/${f.num(data.compared)} emin cevapta aynı`)} />
        <Tile label={t(`Would answer alone (≥ ${alone})`, `Kendi başına (≥ ${alone})`)} value={f.pct(ratio(totals.alone, data.compared))}
              sub={t(`of compared pages; ${f.pct(ratio(totals.aloneAgree, totals.alone))} of those agree`, `karşılaştırılan sayfaların; bunlarda uyum ${f.pct(ratio(totals.aloneAgree, totals.alone))}`)} />
        <Tile label={t("People's later decisions", "Sonraki insan kararları")} value={f.pct(ratio(data.people.agree, data.people.decided))}
              sub={t(`${f.num(data.people.agree)} of ${f.num(data.people.decided)} agree`, `${f.num(data.people.agree)}/${f.num(data.people.decided)} kararda aynı`)} />
        <Tile label={t("Current model", "Güncel model")} value={cur ? f.day(cur.trained_at) : "–"}
              sub={cur ? t(`${f.num(cur.train_examples)} pages learnt, ${f.num(cur.test_examples)} held out`,
                `${f.num(cur.train_examples)} sayfa öğrendi, ${f.num(cur.test_examples)} sayfa ayrıldı`) : ""} />
      </div>
      <p className="muted small">{t(
        `Only guesses next to an EVREN answer at ≥ ${f.dec(data.auto_approve)} confidence (published without a person) count as compared. Agreement is not accuracy: EVREN's confident answers are mostly but not always right (see the accuracy page), so a disagreement is usually, not always, the local model's mistake.`,
        `Yalnız EVREN'in ≥ ${f.dec(data.auto_approve)} güvenle verdiği (insansız yayımlanan) cevapların yanındaki tahminler karşılaştırılır. Uyum doğruluk değildir: EVREN'in emin cevapları çoğunlukla ama her zaman değil doğrudur (doğruluk sayfasına bakın); yani bir ayrılık çoğunlukla, her zaman değil, yerel modelin hatasıdır.`)}</p>

      <h2>{t("Agreement per Day", "Günlük Uyum")}</h2>
      {data.compared === 0 ? <div className="notice">{t("No compared guesses in this period yet.", "Bu dönemde henüz karşılaştırılan tahmin yok.")}</div> : (
        <div className="panel">
          <Legend series={daySeries} />
          <LineChart label={t("Agreement with EVREN per day", "Günlük EVREN uyumu")} labels={labels} series={daySeries} marks={changes}
            tip={(i) => { const d = series[i]; return (<>
              <strong>{f.day(d.day)}</strong>
              <div><i className="key key-a" />{agreeName}: {f.pct(ratio(d.agree, d.compared))} ({f.num(d.agree)}/{f.num(d.compared)})</div>
              <div><i className="key key-b" />{aloneName}: {f.pct(ratio(d.alone_agree, d.alone))} ({f.num(d.alone_agree)}/{f.num(d.alone)})</div>
              {d.models.length > 0 && <div className="muted">{t("Model", "Model")}: {d.models.join(", ")}</div>}
            </>); }} />
          <h3>{t("Pages Compared per Day", "Günlük Karşılaştırılan Sayfa")}</h3>
          <Columns label={t("Pages compared per day", "Günlük karşılaştırılan sayfa")} labels={labels} values={series.map((d) => d.compared)}
            tip={(i) => { const d = series[i]; return (<><strong>{f.day(d.day)}</strong>
              <div>{t("Compared", "Karşılaştırılan")}: {f.num(d.compared)}</div>
              <div className="muted">{t("All guesses", "Tüm tahminler")}: {f.num(d.guesses)}</div></>); }} />
          <details><summary>{t("As a table", "Tablo olarak")}</summary><table>
            <thead><tr><th>{t("Day", "Gün")}</th><th className="num">{t("Guesses", "Tahmin")}</th><th className="num">{t("Compared", "Karşılaştırılan")}</th>
              <th className="num">{agreeName}</th><th className="num">{t(`≥ ${alone}`, `≥ ${alone}`)}</th><th className="num">{aloneName}</th><th>{t("Model", "Model")}</th></tr></thead>
            <tbody>{[...series].reverse().filter((d) => d.guesses).map((d) => (
              <tr key={d.day}><td>{d.day}</td><td className="num">{f.num(d.guesses)}</td><td className="num">{f.num(d.compared)}</td>
                <td className="num">{f.num(d.agree)} ({f.pct(ratio(d.agree, d.compared))})</td><td className="num">{f.num(d.alone)}</td>
                <td className="num">{f.num(d.alone_agree)} ({f.pct(ratio(d.alone_agree, d.alone))})</td><td className="mono small">{d.models.join(", ")}</td></tr>
            ))}</tbody>
          </table></details>
        </div>
      )}

      <h2>{t("If It Answered Alone", "Kendi Başına Cevaplasaydı")}</h2>
      <p className="muted">{t(`Over the period: how many compared pages the model is at least this sure about, and how often it then agrees with EVREN.`,
        "Dönem boyunca: model karşılaştırılan sayfaların kaçında en az bu kadar emin ve o zaman ne sıklıkla EVREN'le aynı.")}</p>
      <div className="panel"><table>
        <thead><tr><th>{t("Model confidence ≥", "Model güveni ≥")}</th><th className="num">{t("Would answer alone", "Kendi başına cevaplardı")}</th>
          <th className="num">{agreeName}</th></tr></thead>
        <tbody>{data.thresholds.map((r) => (
          <tr key={r.threshold}><td>{f.dec(r.threshold)}</td>
            <td className="num">{f.num(r.answered)} ({f.pct(ratio(r.answered, data.compared))})</td>
            <td className="num">{f.num(r.correct)}/{f.num(r.answered)} ({f.pct(ratio(r.correct, r.answered))})</td></tr>
        ))}</tbody>
      </table></div>

      <h2>{t("Weekly Models", "Haftalık Modeller")}</h2>
      <p className="muted">{t("Every training is scored on the same fixed fifth of the domains it never learns from.",
        "Her eğitim, hiç öğrenmediği aynı sabit beşte birlik alan adı dilimiyle sınanır.")}</p>
      {data.models.length > 0 && (
        <div className="panel">
          <Legend series={modelSeries} />
          <LineChart label={t("Held-out accuracy per model", "Model başına ayrılmış sayfa doğruluğu")} height={200}
            labels={data.models.map((m) => f.day(m.trained_at))} series={modelSeries}
            tip={(i) => { const m = data.models[i]; return (<>
              <strong>{m.version}{m.current ? ` · ${t("current", "güncel")}` : ""}</strong>
              <div><i className="key key-a" />{peopleName}: {m.human ? `${f.pct(ratio(m.human.correct, m.human.pages))} (${f.num(m.human.correct)}/${f.num(m.human.pages)})` : "–"}</div>
              <div><i className="key key-b" />{aiName}: {m.ai ? `${f.pct(ratio(m.ai.correct, m.ai.pages))} (${f.num(m.ai.correct)}/${f.num(m.ai.pages)})` : "–"}</div>
              <div className="muted">{t(`${f.num(m.train_examples)} pages learnt`, `${f.num(m.train_examples)} sayfa öğrendi`)}</div>
              {m.macro_recall !== null && <div className="muted">{macroName}: {f.pct(m.macro_recall)}</div>}
            </>); }} />
          <table>
            <thead><tr><th>{t("Model", "Model")}</th><th>{t("Trained", "Eğitim")}</th><th className="num">{t("Learnt", "Öğrenilen")}</th>
              <th className="num">{peopleName}</th><th className="num">{aiName}</th><th className="num">{macroName}</th>
              <th className="num">{t("Balance", "Denge")}</th><th className="num">{t("Time", "Süre")}</th></tr></thead>
            <tbody>{[...data.models].reverse().map((m) => (
              <tr key={m.version}><td className="mono small">{m.version}{m.current && <> · <strong>{t("current", "güncel")}</strong></>}</td>
                <td>{f.when(m.trained_at)}</td><td className="num">{f.num(m.train_examples)}</td>
                <td className="num">{m.human ? f.pct(ratio(m.human.correct, m.human.pages)) : "–"}</td>
                <td className="num">{m.ai ? f.pct(ratio(m.ai.correct, m.ai.pages)) : "–"}</td>
                <td className="num">{m.macro_recall !== null ? f.pct(m.macro_recall) : "–"}</td>
                <td className="num">{f.num(m.class_balance ?? 0)}</td>
                <td className="num">{m.seconds !== null ? `${f.num(Math.round(m.seconds))} s` : "–"}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {cur && (<>
        <h2>{t("Current Model on Held-Out Pages", "Güncel Model, Ayrılmış Sayfalarda")}</h2>
        <HeldOut t={t} title={peopleName} group={cur.human} />
        <HeldOut t={t} title={aiName} group={cur.ai} />
        {data.categories.length > 0 && (<>
          <h3>{t("Per Category", "Kategori Başına")}</h3>
          <p className="muted small">{t("Recall: of the pages in a category, how many it found. Precision: of the pages it put there, how many belong.",
            "Bulma: bir kategorideki sayfaların kaçını buldu. İsabet: oraya koyduğu sayfaların kaçı gerçekten oraya ait.")}</p>
          <div className="panel"><table>
            <thead><tr><th>{t("Category", "Kategori")}</th><th className="num">{t("Pages", "Sayfa")}</th>
              <th className="num">{t("Recall", "Bulma")}</th><th className="num">{t("Precision", "İsabet")}</th></tr></thead>
            <tbody>{[...data.categories].sort((a, b) => b.pages - a.pages).map((c) => (
              <tr key={c.category}><td>{categoryName(locale, c.category, undefined)}</td><td className="num">{f.num(c.pages)}</td>
                <td className="num"><Meter v={ratio(c.correct, c.pages)} text={f.pct(ratio(c.correct, c.pages))} /></td>
                <td className="num"><Meter v={ratio(c.correct, c.guessed)} text={f.pct(ratio(c.correct, c.guessed))} /></td></tr>
            ))}</tbody>
          </table></div>
        </>)}
        {data.too_small.length > 0 && <p className="muted small">{t("Too few pages to learn yet: ", "Öğrenmek için henüz az sayfa var: ")}
          {data.too_small.map((c) => categoryName(locale, c, undefined)).join(", ")}</p>}
      </>)}
    </div>
  );
}

function Meter({ v, text }: { v: number | null; text: string }) {
  return <span className="meter-cell"><span className="meter" aria-hidden="true"><span style={{ width: `${(v ?? 0) * 100}%` }} /></span>{text}</span>;
}

export default function YasMama({ api }: { api: string }) {
  return <KeyGate api={api}>{() => <View api={api} />}</KeyGate>;
}
