export function MetricCard({ label, value, detail, tone = "default" }: Readonly<{ label: string; value: string | number; detail: string; tone?: "default" | "danger" | "success" | "muted" }>) {
  return <article className={`metric-card metric-${tone}`}><p>{label}</p><strong>{value}</strong><span>{detail}</span></article>;
}
