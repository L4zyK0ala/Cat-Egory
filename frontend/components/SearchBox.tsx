"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function SearchBox({ initial = "" }: { initial?: string }) {
  const router = useRouter();
  const [value, setValue] = useState(initial);
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
      <input type="search" aria-label="Domain or URL" placeholder="Search a domain or URL, e.g. example.com"
             value={value} onChange={(e) => setValue(e.target.value)} />
      <button type="submit">Look Up</button>
    </form>
  );
}
