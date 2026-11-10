"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useT } from "./LocaleProvider";

export default function SearchBox({ initial = "" }: { initial?: string }) {
  const router = useRouter();
  const [value, setValue] = useState(initial);
  const t = useT();
  return (
    <form
      className="search"
      onSubmit={(e) => {
        e.preventDefault();
        const raw = value.trim();
        if (!raw) return;
        let host = raw;
        let link = "";
        try {
          const u = new URL(raw.includes("://") ? raw : `http://${raw}`);
          host = u.hostname;
          if ((u.pathname !== "/" && u.pathname !== "") || u.search) link = raw;  // a full link: check that page too
        } catch {
          /* let the API report invalid input */
        }
        router.push(`/domain/${encodeURIComponent(host)}${link ? `?url=${encodeURIComponent(link)}` : ""}`);
      }}
    >
      <input type="search" aria-label={t("Domain or URL", "Alan adı veya URL")}
             placeholder={t("Search a domain or URL, e.g. example.com", "Bir alan adı ya da URL arayın, ör. example.com")}
             value={value} onChange={(e) => setValue(e.target.value)} />
      <button type="submit">{t("Look Up", "Ara")}</button>
    </form>
  );
}
