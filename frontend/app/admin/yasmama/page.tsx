import { publicApiUrl } from "@/lib/api";
import YasMama from "@/components/YasMama";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

export default async function YasMamaPage() {
  const { t } = await getT();
  return (<><h1>Yaş Mama</h1>
    <p className="muted">{t(
      "Our own local classifier, in shadow mode: it learns every week from the labels Cat-Egory already has and guesses next to every EVREN answer, without publishing anything. These numbers show how often it would have been right, to decide one day whether it may answer the easy pages alone.",
      "Kendi yerel sınıflandırıcımız, gölge modda: Cat-Egory'deki etiketlerden her hafta öğrenir ve her EVREN cevabının yanında tahmin yapar, ama hiçbir şey yayımlamaz. Bu sayılar ne sıklıkla haklı çıkacağını gösterir; bir gün kolay sayfaları tek başına cevaplayıp cevaplayamayacağına bunlara bakarak karar verilir.")}</p>
    <YasMama api={publicApiUrl()} /></>);
}
