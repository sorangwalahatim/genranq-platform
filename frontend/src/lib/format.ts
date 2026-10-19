export const fmtDate = (s?: string | null) => (s ? new Date(s).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—");
export const fmtDateTime = (s?: string | null) => (s ? new Date(s).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");
export function ago(s?: string | null) {
  if (!s) return "—";
  const d = (Date.now() - new Date(s).getTime()) / 1000;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  if (d < 86400 * 30) return `${Math.floor(d / 86400)} d ago`;
  return fmtDate(s);
}
export function bytes(n?: number | null) {
  if (n == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v < 10 && i ? v.toFixed(1) : Math.round(v)} ${u[i]}`;
}
export const human = (s?: string | null) => (s || "").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
export const scoreColor = (s?: number | null) =>
  s == null ? "var(--faint)" : s >= 85 ? "var(--pass)" : s >= 65 ? "var(--medium)" : s >= 45 ? "var(--high)" : "var(--critical)";
export const SEV = ["critical", "high", "medium", "low"] as const;
export const sevColor: Record<string, string> = { critical: "var(--critical)", high: "var(--high)", medium: "var(--medium)", low: "var(--low)" };
