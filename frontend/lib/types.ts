export type Category = {
  id: string; code: number; name: string; description: string; kind: "content" | "risk";
  parent_id: string | null; is_group: boolean; deprecated_in: string | null; replaced_by_id: string | null;
};
export type DomainCategory = { id: string; code: number; name: string; primary: boolean; confidence: number | null; origin: string };
export type Risk = {
  malware: boolean; phishing: boolean; scam: boolean; fraud: boolean; command_and_control: boolean;
  malicious_downloads: boolean; suspicious: boolean; lookalike_domain: boolean; sources: { type: string; source: string; first_seen: string }[];
};
export type DomainInfo = {
  domain: string; display_domain: string; registrable_domain: string; categories: DomainCategory[]; risk: Risk;
  classification_status: string; first_seen: string; last_updated: string; last_scanned: string | null;
  data_source: string | null; taxonomy_version: string; license: string; attribution: string;
};
export type HistoryItem = { category: string; primary: boolean; origin: string; confidence: number | null; valid_from: string; valid_to: string | null };
export type Stats = {
  generated_at: string; total_domains: number; categorized_domains: number; verified_domains: number;
  by_status: Record<string, number>; pending_reviews: number; domains_scanned_today: number; total_screenshots: number;
  ai_predictions_30d: number; ai_valid_answer_rate_30d: number | null;
  category_distribution: { category: string; domains: number }[]; threats: Record<string, number>;
  daily_scans: Record<string, Record<string, number>>;
};
export type Feed = { name: string; url: string; version: string; records: number; sha256: string; license: string; taxonomy_version: string; generated_at: string };
