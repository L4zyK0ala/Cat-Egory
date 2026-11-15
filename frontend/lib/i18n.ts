// Two languages, written side by side where the text is used: t("English", "Türkçe"). No keys to keep in sync.
export type Locale = "tr" | "en";
export type T = (en: string, tr: string) => string;

export const LOCALES: Locale[] = ["tr", "en"];
export const LANG_COOKIE = "lang";
export const makeT = (locale: Locale): T => (en, tr) => (locale === "tr" ? tr : en);
/** For dates and numbers. */
export const intl = (locale: Locale) => (locale === "tr" ? "tr-TR" : "en-GB");

/** Turkish names and descriptions of the taxonomy; English comes from the API (classification/taxonomy.yaml). */
export const CATEGORY_TR: Record<string, [string, string]> = {
  "group-business-technology": ["İş ve Teknoloji", "Ticaret, finans ve teknoloji."],
  business: ["İş Dünyası", "Şirket ve kurum siteleri, kurumsal bilgiler, B2B hizmetler, pazarlama ve iş ilanları."],
  finance: ["Finans", "Yatırım, sigorta, alım satım, kişisel finans, ödeme hizmetleri ve finans haberleri."],
  banking: ["Bankacılık", "Bankalar ve kredi kuruluşları, internet bankacılığı dahil."],
  cryptocurrency: ["Kripto Para", "Kripto varlıklar, borsalar, cüzdanlar, madencilik ve blokzincir projeleri."],
  "information-technology": ["Bilgi Teknolojileri", "Genel BT, bilgisayar, donanım, geliştirici kaynakları ve teknoloji haberleri."],
  "artificial-intelligence": ["Yapay Zekâ", "Yapay zekâ ürünleri, sohbet botları, makine öğrenmesi platformları ve araştırma araçları."],
  software: ["Yazılım", "Yazılım ürünleri, SaaS uygulamaları ve üretici siteleri."],
  "cloud-computing": ["Bulut Bilişim", "Bulut altyapı ve platform sağlayıcıları, bulut konsolları ve depolama hizmetleri."],
  cybersecurity: ["Siber Güvenlik", "Güvenlik firmaları, güvenlik araştırmaları, uyarılar ve savunma araçları (tehditlerin kendisi değil)."],
  "web-hosting": ["Web Barındırma", "Barındırma sağlayıcıları, alan adı kayıt firmaları, site oluşturucular ve DNS hizmetleri."],
  telecommunications: ["Telekomünikasyon", "Mobil operatörler, internet servis sağlayıcıları ve telekom ekipmanı üreticileri."],
  "group-communication-social": ["İletişim ve Sosyal", "Esas amacı insanlar arasında iletişim olan siteler."],
  "social-networking": ["Sosyal Ağlar", "Sosyal ağlar ve profil tabanlı platformlar."],
  messaging: ["Mesajlaşma", "Anlık mesajlaşma, sohbet ve sesli/görüntülü arama hizmetleri."],
  email: ["E-posta", "Web e-postası ve e-posta hizmet sağlayıcıları."],
  forums: ["Forumlar", "Tartışma panoları ve soru-cevap siteleri."],
  blogs: ["Bloglar", "Kişisel veya editoryal bloglar ve blog barındırma platformları."],
  "online-communities": ["Çevrimiçi Topluluklar", "İlgi alanı toplulukları, kulüpler ve üyelik siteleri."],
  "group-entertainment": ["Eğlence", "Boş zaman, medya tüketimi ve spor."],
  "streaming-media": ["Yayın Platformları", "Film, dizi ve etkinlikler için isteğe bağlı veya canlı yayın platformları."],
  music: ["Müzik", "Müzik dinleme, sanatçılar, plak şirketleri, şarkı sözleri ve müzik haberleri."],
  video: ["Video", "Video paylaşım platformları ve video barındırma."],
  gaming: ["Oyun", "Video oyunları, oyun mağazaları, oyun haberleri ve tarayıcı oyunları (kumar hariç)."],
  sports: ["Spor", "Spor kulüpleri, ligler, sonuçlar ve spor haberleri (bahis, Kumar kategorisindedir)."],
  entertainment: ["Eğlence", "Ünlüler, film, dizi, mizah, etkinlikler ve genel eğlence."],
  "online-radio": ["İnternet Radyosu", "İnternet radyo istasyonları ve podcast dizinleri."],
  "group-education-information": ["Eğitim ve Bilgi", "Bilgi, haber ve kamu kurumları."],
  education: ["Eğitim", "Okullar, üniversiteler, çevrimiçi kurslar ve öğrenme platformları."],
  research: ["Araştırma", "Araştırma kurumları, akademik yayıncılık ve veri setleri."],
  science: ["Bilim", "Bilimsel içerik ve popüler bilim."],
  news: ["Haber", "Haber kuruluşları ve haber toplayıcılar."],
  media: ["Medya", "Esas olarak haber yayımlamayan dergiler, yayıncılar ve yayın kuruluşları."],
  reference: ["Başvuru Kaynakları", "Ansiklopediler, sözlükler, haritalar, çeviri ve nasıl yapılır kaynakları."],
  libraries: ["Kütüphaneler", "Halk ve üniversite kütüphaneleri ve dijital arşivler."],
  government: ["Kamu", "Kamu kurumları, kamu hizmetleri, askeri ve uluslararası kuruluşlar."],
  legal: ["Hukuk", "Hukuk büroları, hukuki bilgiler ve mevzuat veri tabanları."],
  "group-shopping-lifestyle": ["Alışveriş ve Yaşam", "Tüketici hayatı: alışveriş, seyahat, sağlık, ev."],
  shopping: ["Alışveriş", "Perakende markalar, ürün bilgisi, fiyat karşılaştırma ve kuponlar."],
  "e-commerce": ["E-Ticaret", "Doğrudan ürün satın alınan çevrimiçi mağazalar ve pazar yerleri."],
  auctions: ["Açık Artırma", "Çevrimiçi açık artırma platformları."],
  travel: ["Seyahat", "Havayolları, oteller, rezervasyon siteleri ve seyahat rehberleri."],
  food: ["Yemek", "Restoranlar, tarifler, yemek siparişi ve mutfak."],
  "health-information": ["Sağlık Bilgisi", "Tıbbi bilgiler, hastaneler, eczaneler ve sağlıklı yaşam."],
  "real-estate": ["Emlak", "Emlak ilanları, emlakçılar ve gayrimenkul hizmetleri."],
  automotive: ["Otomotiv", "Araç üreticileri, bayiler, araç ilanları ve otomotiv haberleri."],
  "group-restricted-content": ["Kısıtlı İçerik", "Yaş, yasa veya politika nedeniyle sıkça kısıtlanan içerik."],
  "adult-content": ["Yetişkin İçerik", "Müstehcen içerik, pornografi ve yetişkin hizmetleri."],
  gambling: ["Kumar", "En az bir ülkede kumar düzenleyicisinden lisanslı işletmelerin bahis, casino, piyango ve poker siteleri (milli piyango, lisanslı bahis ve casino)."],
  "illegal-gambling": ["Yasa Dışı Kumar", "Hiçbir kumar düzenleyicisinden lisansı olmayan bahis ve casino siteleri ile engelleri aşmak için açılan adresler (\"yeni giriş\" adresleri, numaralı ya da ayna kopyalar, artık casino gösteren ilgisiz alan adları)."],
  alcohol: ["Alkol", "Alkollü içeceklerin satışı veya tanıtımı."],
  tobacco: ["Tütün", "Tütün, elektronik sigara ve buhar ürünlerinin satışı veya tanıtımı."],
  weapons: ["Silahlar", "Ateşli silah, mühimmat, bıçak ve diğer silahların satışı veya kullanım talimatları."],
  "illegal-drugs": ["Yasa Dışı Uyuşturucular", "Yasa dışı uyuşturucuların satışı, tanıtımı veya kontrollü maddelerin kötüye kullanımı."],
  violence: ["Şiddet", "Çarpıcı şiddet, vahşet veya şiddetin yüceltilmesi."],
  "hate-and-discrimination": ["Nefret ve Ayrımcılık", "İnsanları korunan özelliklerinden ötürü hedef alan içerik."],
  "group-web-infrastructure": ["Web Altyapısı", "Başka içerikleri taşıyan, gizleyen veya barındıran hizmetler."],
  "search-engines": ["Arama Motorları", "Web arama motorları ve portallar."],
  "url-shorteners": ["Link Kısaltıcılar", "Link kısaltma ve yönlendirme hizmetleri."],
  "content-delivery-networks": ["İçerik Dağıtım Ağları", "CDN ve dosya barındırma alan adları."],
  "proxy-services": ["Proxy Hizmetleri", "Filtreleri aşmak için kullanılan web proxy'leri ve anonimleştiriciler."],
  "vpn-services": ["VPN Hizmetleri", "Ücretli veya ücretsiz VPN sağlayıcıları."],
  "remote-access": ["Uzaktan Erişim", "Uzak masaüstü ve uzaktan yönetim hizmetleri."],
  "file-sharing": ["Dosya Paylaşımı", "Dosya barındırma, bulut sürücüler, eşler arası paylaşım ve torrent dizinleri."],
  "software-downloads": ["Yazılım İndirme", "İndirme portalları ve yazılım yansıları."],
  "parked-domains": ["Park Edilmiş Alan Adları", "Park edilmiş, satılık, yer tutucu veya boş varsayılan sayfalar."],
  "group-security-threats": ["Güvenlik Tehditleri", "İçerik kategorilerinden bağımsız risk sınıflandırması."],
  malware: ["Zararlı Yazılım", "Zararlı yazılım veya istismar kiti barındırır ya da dağıtır."],
  phishing: ["Oltalama", "Kimlik bilgilerini veya ödeme bilgilerini çalmak için bir marka ya da hizmeti taklit eder."],
  scam: ["Dolandırıcılık", "Aldatıcı teklifler: sahte mağazalar, sahte destek, peşin ücret ve yatırım dolandırıcılığı."],
  fraud: ["Sahtecilik", "Ödeme sahteciliği, taklit ürünler, sahte belgeler."],
  "command-and-control": ["Komuta ve Kontrol", "Zararlı yazılımları veya botnetleri yönetmek için kullanılan altyapı."],
  suspicious: ["Şüpheli Siteler", "Belirli bir tehdit türü için yeterli kanıt olmadan kötü niyet belirtileri."],
  "malicious-downloads": ["Zararlı İndirmeler", "Zararlı olduğu bilinen veya muhtemel indirmeler sunar."],
  "lookalike-domain": ["Taklit Alan Adı", "Adında tanınmış bir site veya markayı taklit eder: benzer karakterler (amazon'da Kiril а), yazım hataları veya eklenmiş kelimeler. Yalnızca incelemeden sonra işaretlenir; markanın kendi savunma amaçlı kayıtları işaretlenmez."],
};

export function categoryName(locale: Locale, id: string, english: string | undefined): string {
  return locale === "tr" ? CATEGORY_TR[id]?.[0] ?? english ?? id : english ?? id;
}

export function categoryDescription(locale: Locale, id: string, english: string): string {
  return locale === "tr" ? CATEGORY_TR[id]?.[1] ?? english : english;
}

/** Scan job states as shown in the panel. */
export function scanStatus(t: T, status: string): string {
  const labels: Record<string, string> = {
    queued: t("queued", "kuyrukta"), running: t("running", "çalışıyor"), succeeded: t("succeeded", "başarılı"),
    blocked: t("blocked", "engellendi"), failed: t("failed", "başarısız"), cancelled: t("cancelled", "iptal edildi"),
  };
  return labels[status] ?? status;
}
