import { publicApiUrl } from "@/lib/api";
import AdminConsole from "@/components/AdminConsole";

export const dynamic = "force-dynamic";

export default function Admin() {
  return (<><h1>Admin</h1><AdminConsole api={publicApiUrl()} /></>);
}
