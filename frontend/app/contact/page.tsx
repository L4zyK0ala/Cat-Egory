import type { Metadata } from "next";
import Link from "next/link";
import { getT } from "@/lib/locale";
import { contactEmail } from "@/lib/site";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getT();
  return { title: t("Contact", "İletişim"),
           description: t("How to reach Cat-Egory, correct a category or report a security issue.",
                          "Cat-Egory'ye nasıl ulaşılır, bir kategori nasıl düzeltilir, güvenlik sorunu nasıl bildirilir.") };
}

export default async function Contact() {
  const { locale, t } = await getT();
  const email = contactEmail();
  const address = email ? <a href={`mailto:${email}`}>{email}</a>
    : <span className="muted">{t("(address coming soon)", "(adres yakında)")}</span>;
  if (locale === "tr") return (
    <div className="about">
      <h1>İletişim</h1>
      <div className="panel stack">
        <h3>Yanlış bir kategori ya da yanlışlıkla işaretlenmiş bir site</h3>
        <p>Alan adının sayfasını açıp <b>Bir Yanlışlık mı Var?</b> bölümünü kullanın ya da <Link href="/submit">bir site
          önerin</Link>. Her bildirime bir kişi bakar. Kişisel bilgi yazmanıza gerek yok.</p>
      </div>
      <h2>Kişisel Veriler</h2>
      <div className="panel stack">
        <p>Verilerinizle ilgili sorular ve talepler (KVKK): {address}. Ayrıntılar için{" "}
          <Link href="/privacy">aydınlatma metni</Link>.</p>
      </div>
      <h2>Güvenlik</h2>
      <div className="panel stack">
        <p>Cat-Egory&apos;de bir güvenlik açığı mı buldunuz? Lütfen önce bize gizlice bildirin: {address}. Makinenin
          okuyabileceği ayrıntılar <a href="/.well-known/security.txt">security.txt</a> dosyasında.</p>
      </div>
    </div>
  );
  return (
    <div className="about">
      <h1>Contact</h1>
      <div className="panel stack">
        <h3>A wrong category, or a site flagged by mistake</h3>
        <p>Open the domain&apos;s page and use <b>Is something wrong?</b>, or <Link href="/submit">suggest a site</Link>.
          A person looks at every report. You do not need to write personal information.</p>
      </div>
      <h2>Personal Data</h2>
      <div className="panel stack">
        <p>Questions and requests about your data (KVKK): {address}. See the <Link href="/privacy">privacy notice</Link>.</p>
      </div>
      <h2>Security</h2>
      <div className="panel stack">
        <p>Found a vulnerability in Cat-Egory? Please tell us privately first: {address}. Machine-readable details are in{" "}
          <a href="/.well-known/security.txt">security.txt</a>.</p>
      </div>
    </div>
  );
}
