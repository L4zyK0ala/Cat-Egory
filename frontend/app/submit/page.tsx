import { api, publicApiUrl } from "@/lib/api";
import type { Category } from "@/lib/types";
import SuggestForm from "@/components/SuggestForm";

export const dynamic = "force-dynamic";

export default async function Submit({ searchParams }: { searchParams: Promise<{ domain?: string }> }) {
  const { domain } = await searchParams;
  const taxonomy = await api<{ items: Category[] }>("/api/v1/categories?kind=content", 3600).catch(() => ({ items: [] as Category[] }));
  return (
    <>
      <h1>Suggest a site</h1>
      <p className="muted">Suggestions are reviewed by a moderator before the site is scanned and classified.</p>
      <SuggestForm api={publicApiUrl()} initial={(domain ?? "").slice(0, 253)}
                   categories={taxonomy.items.filter((c) => !c.is_group).map((c) => ({ id: c.id, name: c.name }))} />
    </>
  );
}
