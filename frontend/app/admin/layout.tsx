import { notFound } from "next/navigation";
import type { Metadata } from "next";
import AdminNav from "@/components/AdminNav";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { robots: { index: false, follow: false } };

/** Admin pages exist only in the admin UI (UI_ADMIN=true), which runs as its own container on a private
 *  address and talks to the admin API. The public site answers 404 here. */
export default function AdminLayout({ children }: { children: React.ReactNode }) {
  if (process.env.UI_ADMIN !== "true") notFound();
  return <><AdminNav />{children}</>;
}
