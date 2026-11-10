import { publicApiUrl } from "@/lib/api";
import SgbReport from "@/components/SgbReport";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

export default async function Threats() {
  const { locale, t } = await getT();
  return (<><h1>{t("Confirmed Threats", "Onaylanmış Tehditler")}</h1>
    {locale === "tr" ? <p className="muted">
      Bir inceleyicinin onayladığı tehditler. <b>Geri Çek</b> bir işareti geri çeker (hata ya da site temizlendi); geçmişte
      ve denetim kaydında saklanır. Her alan adı günde bir kez T.C. Siber Güvenlik Başkanlığı&apos;nın (USOM) zararlı adres
      listesiyle karşılaştırılır: <b>listede olmayanlar</b>{" "}
      <a href="https://siberguvenlik.gov.tr" target="_blank" rel="noopener noreferrer">siberguvenlik.gov.tr</a> üzerindeki
      İhbar Formu ile bildirilebilecek adaylardır. Yalnızca iç kullanım içindir: listenin şartları yazılı izin olmadan yeniden
      yayımlanmasına izin vermez.
    </p> : <p className="muted">
      Threats a reviewer confirmed. <b>Withdraw</b> takes a flag back (a mistake, or the site was cleaned); it is kept
      in the history and the audit log. Each domain is checked once a day against the malicious-address list of
      T.C. Siber Güvenlik Başkanlığı (USOM): <b>not on the list</b> are candidates to report through the İhbar Formu on{" "}
      <a href="https://siberguvenlik.gov.tr" target="_blank" rel="noopener noreferrer">siberguvenlik.gov.tr</a>.
      Internal only: the list&apos;s terms do not allow republishing it without written permission.
    </p>}
    <SgbReport api={publicApiUrl()} /></>);
}
