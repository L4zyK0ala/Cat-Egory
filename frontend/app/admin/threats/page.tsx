import { publicApiUrl } from "@/lib/api";
import UsomQueue from "@/components/UsomQueue";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

function siteName(): string {
  try {
    return new URL(process.env.PUBLIC_SITE_URL || "").host || "cat-egory.org";
  } catch {
    return "cat-egory.org";
  }
}

export default async function Threats() {
  const { locale, t } = await getT();
  return (<><h1>{t("Confirmed Threats", "Onaylanmış Tehditler")}</h1>
    {locale === "tr" ? <p className="muted">
      Onayladığımız tehditleri T.C. Siber Güvenlik Başkanlığı&apos;na (USOM) bildirme kuyruğu. Her alan adı günde bir kez
      ulusal zararlı adres listesiyle karşılaştırılır; listede olmayanlar <b>Bildirilecek</b> sekmesinde bekler. Seçip
      metni okuyun ve <b>USOM&apos;a Gönder</b>&apos;e basın: e-posta yalnızca siz bastığınızda gider. Liste bir adresi
      eklediğinde kendiliğinden <b>USOM listeledi</b> sekmesine geçer. Bir işareti geri çekmek için <b>Tüm onaylanmış
      tehditler</b> sekmesini kullanın. Ulusal liste yalnızca iç kullanım içindir; şartları yeniden yayımlamaya izin vermez.
    </p> : <p className="muted">
      The queue for reporting our confirmed threats to the national CERT (USOM). Each domain is checked against the
      national malicious-address list once a day; those it lacks wait under <b>To report</b>. Pick some, read the text
      and press <b>Send to USOM</b>: the e-mail goes only when you press it. When the list adds a domain, it moves to
      <b> Listed by USOM</b> by itself. To withdraw a flag, use <b>All confirmed threats</b>. The national list is for
      internal use only; its terms do not allow republishing it.
    </p>}
    <UsomQueue api={publicApiUrl()} site={siteName()} /></>);
}
