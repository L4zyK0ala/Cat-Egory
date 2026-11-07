import Link from "next/link";
import { api } from "@/lib/api";
import type { Category } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Categories() {
  let data: { taxonomy_version: string; items: Category[] } | null = null;
  try {
    data = await api("/api/v1/categories", 3600);
  } catch {
    data = null;
  }
  if (!data) return <div className="notice error">The API is not reachable right now.</div>;
  const groups = data.items.filter((c) => c.is_group);
  return (
    <>
      <h1>Categories</h1>
      <p className="muted">Taxonomy {data.taxonomy_version}. Security threats are a separate risk classification.</p>
      {groups.map((g) => (
        <section key={g.id}>
          <h2>{g.name} {g.kind === "risk" && <span className="badge danger">risk</span>}</h2>
          <div className="panel">
            <table><tbody>
              {data!.items.filter((c) => c.parent_id === g.id && !c.deprecated_in).map((c) => (
                <tr key={c.id}><td style={{ width: 240 }}><Link href={`/categories/${c.id}`}>{c.name}</Link></td>
                  <td className="muted">{c.description}</td><td className="mono muted">{c.id}</td></tr>
              ))}
            </tbody></table>
          </div>
        </section>
      ))}
    </>
  );
}
