"use client";
import { useState } from "react";
import { useT } from "./LocaleProvider";

/** A value to paste somewhere else (a feed address), with a copy button. */
export default function CopyText({ value }: { value: string }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  return (
    <span className="copy-text">
      <code>{value}</code>
      <button type="button" className="secondary" onClick={async () => {
        try { await navigator.clipboard.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1500); }
        catch { /* clipboard blocked: the text can still be selected */ }
      }}>{copied ? t("Copied", "Kopyalandı") : t("Copy", "Kopyala")}</button>
    </span>
  );
}
