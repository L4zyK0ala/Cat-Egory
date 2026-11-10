"use client";
import { useRouter } from "next/navigation";
import { LANG_COOKIE, LOCALES } from "@/lib/i18n";
import { useLocale } from "./LocaleProvider";

/** TR | EN. Remembers the choice in one cookie (named in the privacy notice); nothing else. */
export default function LanguageSwitch() {
  const current = useLocale();
  const router = useRouter();
  return (
    <span className="lang-switch" role="group" aria-label="Language / Dil">
      {LOCALES.map((l) => (
        <button key={l} type="button" aria-pressed={l === current} className={l === current ? "on" : ""}
                onClick={() => {
                  document.cookie = `${LANG_COOKIE}=${l}; path=/; max-age=31536000; SameSite=Lax`;
                  router.refresh();
                }}>{l.toUpperCase()}</button>
      ))}
    </span>
  );
}
