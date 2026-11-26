import { formatDay, intl, type Locale, type T } from "@/lib/i18n";

type Day = { day: string; succeeded: number; blocked: number; failed: number };

/** The last 14 days, oldest first, Türkiye time; days without a finished scan count as zero. */
export function lastDays(daily: Record<string, Record<string, number>>, generatedAt: string): Day[] {
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Istanbul" }).format(new Date(generatedAt));
  const base = Date.parse(`${today}T12:00:00Z`);
  return Array.from({ length: 14 }, (_, i) => {
    const day = new Date(base - (13 - i) * 86_400_000).toISOString().slice(0, 10);
    const c = daily[day] ?? {};
    return { day, succeeded: c.succeeded ?? 0, blocked: c.blocked ?? 0, failed: c.failed ?? 0 };
  });
}

function niceStep(max: number): number {
  const raw = Math.max(1, max) / 3;
  const mag = 10 ** Math.floor(Math.log10(raw));
  return ([1, 2, 2.5, 5, 10].find((m) => m * mag >= raw) ?? 10) * mag;
}

/** Successful scans per day as one red line. Today is still running, so its segment is dashed and its dot hollow.
 *  Blocked and failed scans are told in a sentence below; the full numbers are in the table under the chart. */
export default function ScanChart({ days, locale, t }: { days: Day[]; locale: Locale; t: T }) {
  const n = (v: number) => v.toLocaleString(intl(locale));
  const blocked = days.reduce((s, d) => s + d.blocked, 0);
  const failed = days.reduce((s, d) => s + d.failed, 0);
  return (
    <>
      {/* the same chart twice: a wide one and a narrow one for phones, so the text stays readable (globals.css) */}
      <Chart days={days} locale={locale} t={t} width={1000} every={2} className="wide" />
      <Chart days={days} locale={locale} t={t} width={400} every={4} className="narrow" />
      <p className="muted chart-note">{t(`Türkiye time. In the last 14 days ${n(blocked)} scans were refused by robots.txt or our safety rules; `
                                         + `${n(failed)} failed and were retried within hours.`,
                                         `Türkiye saati. Son 14 günde ${n(blocked)} tarama robots.txt ya da güvenlik kurallarımız yüzünden engellendi; `
                                         + `${n(failed)} tarama başarısız oldu ve birkaç saat içinde yeniden denendi.`)}</p>
    </>
  );
}

function Chart({ days, locale, t, width, every, className }: {
  days: Day[]; locale: Locale; t: T; width: number; every: number; className: string;
}) {
  const n = (v: number) => v.toLocaleString(intl(locale));
  const [x0, x1, y0, yTop] = [width > 500 ? 56 : 44, width - 20, 200, 24];
  const step = niceStep(Math.max(...days.map((d) => d.succeeded)));
  const top = Math.max(step, Math.ceil(Math.max(...days.map((d) => d.succeeded)) / step) * step);
  const x = (i: number) => x0 + (i * (x1 - x0)) / (days.length - 1);
  const y = (v: number) => y0 - (v / top) * (y0 - yTop);
  const done = days.slice(0, -1);
  const today = days[days.length - 1];
  const peak = done.reduce((best, d, i) => (d.succeeded > done[best].succeeded ? i : best), 0);
  const last = done.length - 1;
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
  const lastLabel = days.length - 1;
  const labelled = (i: number) => i === lastLabel || (i % every === 0 && lastLabel - i >= every / 2 + 0.5);
  const summary = t(`Successful scans per day over the last 14 days: ${n(done[0].succeeded)} on ${formatDay(locale, done[0].day)}, `
                    + `${n(done[last].succeeded)} on ${formatDay(locale, done[last].day)}; ${n(today.succeeded)} so far today.`,
                    `Son 14 günde günlük başarılı tarama: ${formatDay(locale, done[0].day)} ${n(done[0].succeeded)}, `
                    + `${formatDay(locale, done[last].day)} ${n(done[last].succeeded)}; bugün şimdiye kadar ${n(today.succeeded)}.`);
  const tip = (d: Day, i: number) => `${i === days.length - 1 ? t("Today", "Bugün") : formatDay(locale, d.day)}: `
    + t(`${n(d.succeeded)} succeeded, ${n(d.blocked)} blocked, ${n(d.failed)} failed`,
        `${n(d.succeeded)} başarılı, ${n(d.blocked)} engellendi, ${n(d.failed)} başarısız`);
  return (
    <svg className={`scan-chart ${className}`} role="img" aria-label={summary} viewBox={`0 0 ${width} 232`}>
        <g className="grid">{ticks.map((v) => <line key={v} x1={x0} x2={x1} y1={y(v)} y2={y(v)} />)}</g>
        <g className="axis" textAnchor="end">{ticks.map((v) => <text key={v} x={x0 - 10} y={y(v) + 4}>{n(v)}</text>)}</g>
        <g className="axis" textAnchor="middle">
          {days.map((d, i) => labelled(i) && (
            <text key={d.day} x={x(i)} y={224} textAnchor={i === 0 ? "start" : i === days.length - 1 ? "end" : "middle"}>
              {i === days.length - 1 ? t("Today", "Bugün") : formatDay(locale, d.day)}</text>))}
        </g>
        <polyline className="line" points={done.map((d, i) => `${x(i)},${y(d.succeeded)}`).join(" ")} />
        <line className="line running" x1={x(last)} y1={y(done[last].succeeded)} x2={x(days.length - 1)} y2={y(today.succeeded)} />
        <circle className="dot" cx={x(last)} cy={y(done[last].succeeded)} r={4.5} />
        <circle className="dot today" cx={x(days.length - 1)} cy={y(today.succeeded)} r={5} />
        <text className="value" x={x(peak)} y={y(done[peak].succeeded) - 12} textAnchor="middle">{n(done[peak].succeeded)}</text>
        <text className="note" x={x(days.length - 1) - 12} y={Math.min(y0 - 8, y(today.succeeded) + 22)} textAnchor="end">
          {width > 500 ? t(`${n(today.succeeded)}, day still running`, `${n(today.succeeded)}, gün sürüyor`) : n(today.succeeded)}</text>
        {days.map((d, i) => (
          <circle key={d.day} className="hit" cx={x(i)} cy={y(d.succeeded)} r={14}><title>{tip(d, i)}</title></circle>
        ))}
      </svg>
  );
}
