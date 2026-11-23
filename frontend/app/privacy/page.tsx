import type { Metadata } from "next";
import Link from "next/link";
import { getT } from "@/lib/locale";
import { contactEmail, operatorName } from "@/lib/site";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getT();
  return { title: t("Privacy", "Gizlilik"),
           description: t("What personal data Cat-Egory handles, and your rights under KVKK.",
                          "Cat-Egory hangi kişisel verileri işliyor ve KVKK kapsamındaki haklarınız neler.") };
}

export default function Privacy() {
  const email = contactEmail();
  const name = operatorName();
  return (
    <div className="about" lang="tr">
      <h1>Gizlilik ve KVKK Aydınlatma Metni</h1>
      <p className="muted">Son güncelleme: 22 Kasım 2026 · <span lang="en">An English summary is at the end of this page.</span></p>

      <h2>Veri Sorumlusu</h2>
      <div className="panel stack">
        <p>Cat-Egory, 6698 sayılı Kişisel Verilerin Korunması Kanunu (KVKK) kapsamında veri sorumlusu olarak{" "}
          <b>{name ?? "[ad soyad yayından önce eklenecek]"}</b> tarafından işletilmektedir.
          İletişim: {email ? <a href={`mailto:${email}`}>{email}</a> : "[e-posta yayından önce eklenecek]"}</p>
      </div>

      <h2>Hangi Verileri, Neden İşliyoruz?</h2>
      <div className="panel stack">
        <p><b>Siteyi ziyaret ettiğinizde:</b> Hesap açmanız gerekmez; üçüncü taraf analiz aracı kullanmıyoruz. Yalnızca dil seçiminizi
          (Türkçe/İngilizce) hatırlayan, kişisel veri içermeyen tek bir çerez kullanılır. IP adresiniz
          veritabanına yazılmaz. Hız sınırı ve kötüye kullanımın önlenmesi için IP adresinizin geri döndürülemeyen, anahtarlı
          bir özeti kullanılır. Teknik sunucu günlükleri sınırlı boyutta tutulur ve otomatik olarak silinir.</p>
        <p><b>Sorguladığınız alan adları ve indirdiğiniz listeler:</b> Hangi sitelerin taranacağına öncelik vermek için
          sorgulanan alan adlarını ve indirilen listeleri günlük toplamlar olarak sayarız: her alan adı ve liste için o gün
          kaç sorgu yapıldığı ve bunların yaklaşık kaç farklı ziyaretçiden geldiği. Farklı ziyaretçileri ayırt etmek için
          IP adresinizin anahtarlı özeti, içinden tek tek değerlerin geri okunamadığı bir sayaca eklenir. Kimin neyi
          sorguladığı hiçbir yerde kaydedilmez. Günlük toplamlar 90 gün sonra silinir. Birkaç farklı ziyaretçinin
          sorguladığı ama veritabanımızda olmayan bir alan adı taranıp kategorilendirilebilir.</p>
        <p><b>Öneri veya düzeltme gönderdiğinizde:</b> Alan adı, seçtiğiniz kategori, yazdığınız açıklama ve kanıt
          bağlantıları ile IP adresinizin anahtarlı özeti saklanır. Ad veya e-posta istemiyoruz; lütfen açıklamaya kişisel
          bilgi yazmayın. Açıklamaları yalnızca moderatörler görür.</p>
        <p><b>Geliştirici anahtarı aldığınızda:</b> Ad veya e-posta istemiyoruz. İsteğe bağlı yazdığınız etiket, anahtarın
          geri döndürülemez özeti (anahtarın kendisi saklanmaz), oluşturma ve son kullanım zamanı, günlük istek sayısı ve IP
          adresinizin anahtarlı özeti tutulur. Anahtarı istediğiniz an kendiniz iptal edebilirsiniz.</p>
        <p><b>Yönetici ve moderatör hesapları:</b> E-posta, görünen ad, parolanın geri döndürülemez özeti ve yapılan işlemlerin
          denetim kayıtları tutulur.</p>
        <p><b>Taranan web siteleri:</b> Herkese açık sayfaların ekran görüntüsü, HTML ve metni kategori belirlemek için alınır.
          Bu içeriklerde kişisel veri bulunabilir. Bunlar <b>yayımlanmaz</b>, yalnızca inceleyenler görür ve 180 gün sonra
          silinir.</p>
        <p><b>Hukuki sebepler:</b> Meşru menfaat ve bilgi güvenliği (KVKK m.5/2-f), hesaplar için sözleşmenin ifası
          (m.5/2-c), yasal yükümlülükler (m.5/2-ç).</p>
      </div>

      <h2>Kimlerle Paylaşılır?</h2>
      <div className="panel stack">
        <p>Kişisel verilerinizi satmıyoruz ve reklam amacıyla paylaşmıyoruz. Hizmeti sunmak için şu sağlayıcılar kullanılır:</p>
        <ul>
          <li><b>Cloudflare:</b> Siteye erişimi sağlar; bağlantı sırasında IP adresiniz bu sağlayıcı tarafından işlenir ve
            sunucuları yurt dışında olabilir (KVKK m.9).</li>
          <li><b>Evren yapay zekâ hizmeti:</b> Taranan sayfaların içeriği kategori belirlemek için gönderilir. Ziyaretçi ve
            öneri verileri gönderilmez. Sağlayıcı, gönderilen içeriğin model eğitiminde veya veri seti oluşturmada
            kullanılmadığını bildirmiştir.</li>
          <li><b>VirusTotal:</b> Yalnızca şüpheli alan adları sorgulanır.</li>
          <li><b>Cloudflare R2 (yedek depolama):</b> Veritabanının ve taranan sayfa içeriklerinin gecelik yedeği, sunucuda
            şifrelendikten sonra bu depolama hizmetine kopyalanır. Sağlayıcı yalnızca şifreli veriyi görür ve içeriğini
            okuyamaz; şifre anahtarı yalnızca bizdedir. Depolama yeri yurt dışındadır (Avrupa; KVKK m.9).</li>
        </ul>
      </div>

      <h2>Saklama Süresi</h2>
      <div className="panel stack">
        <p>Taranan sayfa içerikleri 180 gün saklanır. Öneri kayıtları, verinin geçmişini gösterebilmek için tutulur; talep
          üzerine silinir veya anonimleştirilir.</p>
        <p><b>Yedekler:</b> Sunucudaki gecelik yedekler 14 gün saklanır. Şifreli, sunucu dışı kopyalardan son 14 günün
          her biri, son 8 haftanın ve son 12 ayın birer yedeği tutulur; yani bir kayıt silindikten sonra en fazla 12 ay
          boyunca bu şifreli yedeklerde kalabilir ve süresi dolan yedekle birlikte silinir. Yedekler yalnızca veri kaybında
          geri yükleme için kullanılır.</p>
      </div>

      <h2>Haklarınız</h2>
      <div className="panel stack">
        <p>KVKK m.11 uyarınca verilerinizin işlenip işlenmediğini öğrenme, düzeltilmesini veya silinmesini isteme, aktarıldığı
          kişileri bilme ve zarara uğramanız hâlinde giderim talep etme haklarına sahipsiniz. Başvurularınızı yukarıdaki adrese
          iletebilirsiniz; en geç 30 gün içinde yanıtlanır. Bir sitenin kategorisi veya tehdit işareti yanlışsa{" "}
          <Link href="/contact">iletişim sayfasındaki</Link> yolu kullanabilirsiniz.</p>
      </div>

      <h2 lang="en">English Summary</h2>
      <div className="panel stack" lang="en">
        <p>No account and no third-party analytics; one cookie remembers your language choice and nothing else. Your IP address is not stored in our database: a keyed, irreversible hash limits
          abuse. Looked-up domains and list downloads are counted as daily totals (how many lookups, roughly how many different visitors), never who asked; the totals are deleted after 90 days. Reports keep what you type, so please leave out personal details. Developer keys need no name or e-mail. Page content from scanned sites is private
          and deleted after 180 days. Cloudflare carries the traffic and Evren reads scanned pages (its provider states that
          it does not train models on them); neither receives your reports. Nightly backups are kept on our server for 14 days; encrypted copies (readable only with our key) are
          stored with Cloudflare R2 in Europe for up to 12 months. For requests about your data, use the address above.</p>
      </div>
    </div>
  );
}
