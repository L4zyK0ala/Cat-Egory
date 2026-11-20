"use client";
import { useMemo, useState } from "react";
import { intl } from "@/lib/i18n";
import { useLocale, useT } from "./LocaleProvider";

export type FeedList = { id: string; name: string; description: string; records: number; license: string;
                         formats: { format: string; url: string }[] };
type Group = { id: string; name: string; lists: FeedList[] };

/** The format links of one list: small chips instead of four table rows. */
function Formats({ list }: { list: FeedList }) {
  return (
    <span className="fmt-chips">
      {list.formats.map((f) => (
        <a key={f.format} className="fmt" href={f.url} title={f.url}>{f.format.toUpperCase()}</a>
      ))}
    </span>
  );
}

function Row({ list }: { list: FeedList }) {
  const t = useT();
  const locale = useLocale();
  return (
    <li className={list.records === 0 ? "feed-row empty" : "feed-row"}>
      <div className="feed-name">
        <b>{list.name}</b>
        <span className="muted">{list.description}</span>
      </div>
      <span className="feed-count">{list.records.toLocaleString(intl(locale))} <small>{t("sites", "site")}</small></span>
      <Formats list={list} />
    </li>
  );
}

/** Profiles first, then threats, then content categories folded by group, with a filter box. Empty lists are
 *  hidden until asked for, so the page stays short. */
export default function FeedBrowser({ profiles, threats, groups, allThreats }: {
  profiles: FeedList[]; threats: FeedList[]; groups: Group[]; allThreats: { url: string; records: number } | null;
}) {
  const t = useT();
  const locale = useLocale();
  const [query, setQuery] = useState("");
  const [showEmpty, setShowEmpty] = useState(false);
  const q = query.trim().toLocaleLowerCase(intl(locale));
  const { shownThreats, shownGroups } = useMemo(() => {
    const lower = (s: string) => s.toLocaleLowerCase(intl(locale));
    const all = [...threats, ...groups.flatMap((g) => g.lists)].filter((l) => showEmpty || l.records > 0);
    // Names first: "kumar" should find Gambling, not every description that says "not gambling". Descriptions
    // only when no name matches (e.g. "bahis" finds Gambling through its description).
    const byName = all.filter((l) => !q || lower(`${l.name} ${l.id}`).includes(q));
    const hits = new Set((byName.length || !q ? byName : all.filter((l) => lower(l.description).includes(q))).map((l) => l.id));
    return {
      shownThreats: threats.filter((l) => hits.has(l.id)),
      shownGroups: groups.map((g) => ({ ...g, shown: g.lists.filter((l) => hits.has(l.id)) })).filter((g) => g.shown.length),
    };
  }, [threats, groups, q, showEmpty, locale]);
  const empty = [...threats, ...groups.flatMap((g) => g.lists)].filter((l) => l.records === 0).length;

  return (
    <div className="feed-browser">
      <h2>{t("Ready-Made Profiles", "Hazır Profiller")}</h2>
      <div className="feed-profiles">
        {profiles.map((p) => (
          <div key={p.id} className="panel feed-profile">
            <div className="row"><b>{p.name}</b><span className="badge">{p.license}</span></div>
            <p className="muted">{p.description}</p>
            <div className="row feed-profile-foot">
              <span className="feed-count">{p.records.toLocaleString(intl(locale))} <small>{t("sites", "site")}</small></span>
              <Formats list={p} />
            </div>
          </div>
        ))}
      </div>

      <div className="feed-filter row">
        <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
               placeholder={t("Find a category, e.g. gambling", "Kategori bulun, ör. kumar")} aria-label={t("Find a category", "Kategori bulun")} />
        {empty > 0 && (
          <label className="muted"><input type="checkbox" checked={showEmpty} onChange={(e) => setShowEmpty(e.target.checked)} />{" "}
            {t(`Show ${empty} empty lists`, `${empty} boş listeyi de göster`)}</label>
        )}
      </div>

      {(shownThreats.length > 0 || !q) && (<>
        <h2>{t("Security Threats", "Güvenlik Tehditleri")}</h2>
        <div className="panel">
          {allThreats && !q && (
            <p className="feed-all">{t("Everything at once as a DNS zone:", "Hepsi tek bir DNS bölgesi olarak:")}{" "}
              <a className="fmt" href={allThreats.url}>security.rpz</a>{" "}
              <span className="muted">{allThreats.records.toLocaleString(intl(locale))} {t("sites", "site")}</span></p>
          )}
          {allThreats && !q && <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
            {t("One site can carry several threat types, so the lists below add up to more than this. Only whole-domain threats we confirmed (or that a source allowing redistribution reported) go into these lists; a single dangerous page on an otherwise fine site does not, so pages and statistics elsewhere can show more.",
               "Bir site birden çok tehdit türünde olabilir; bu yüzden aşağıdaki listelerin toplamı bu sayıdan büyüktür. Bu listelere yalnızca alan adının tamamı için onayladığımız (ya da yeniden yayımlamaya izin veren bir kaynağın bildirdiği) tehditler girer; aksi halde zararsız bir sitedeki tek bir tehlikeli sayfa girmez. Bu yüzden diğer sayfalar ve istatistikler daha fazla gösterebilir.")}</p>}
          <ul className="feed-rows">{shownThreats.map((l) => <Row key={l.id} list={l} />)}</ul>
        </div>
      </>)}

      <h2>{t("Content Categories", "İçerik Kategorileri")}</h2>
      {shownGroups.length === 0 ? <div className="notice">{t("No list matches.", "Eşleşen liste yok.")}</div> : shownGroups.map((g) => {
        const total = g.shown.reduce((n, l) => n + l.records, 0);
        return (
          <details key={g.id} className="panel feed-group" open={!!q}>
            <summary>
              <b>{g.name}</b>
              <span className="muted">{t(`${g.shown.length} lists · ${total.toLocaleString("en-GB")} sites`,
                                         `${g.shown.length} liste · ${total.toLocaleString("tr-TR")} site`)}</span>
            </summary>
            <ul className="feed-rows">{g.shown.map((l) => <Row key={l.id} list={l} />)}</ul>
          </details>
        );
      })}
    </div>
  );
}
