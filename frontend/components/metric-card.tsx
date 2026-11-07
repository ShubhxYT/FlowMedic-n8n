export function MetricCard({ label, value, detail, tone = "default", loading = false }: Readonly<{ label: string; value: string | number; detail: string; tone?: "default" | "danger" | "success" | "muted"; loading?: boolean }>) {
  return (
    <article className={`metric-card metric-${tone}`} aria-busy={loading || undefined}>
      <p>{label}</p>
      {loading
        ? <strong><span className="skeleton skeleton-value" aria-hidden="true" /><span className="sr-only">Loading</span></strong>
        : <strong>{value}</strong>}
      <span>{detail}</span>
    </article>
  );
}
