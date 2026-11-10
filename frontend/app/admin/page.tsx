import { publicApiUrl } from "@/lib/api";
import AdminConsole from "@/components/AdminConsole";
import { getT } from "@/lib/locale";

export const dynamic = "force-dynamic";

export default async function Admin() {
  const { t } = await getT();
  return (<><h1>{t("Admin", "Yönetim")}</h1><AdminConsole api={publicApiUrl()} /></>);
}
