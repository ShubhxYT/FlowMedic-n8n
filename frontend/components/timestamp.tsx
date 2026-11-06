import { formatTimestamp } from "@/lib/format";

export function Timestamp({ value }: Readonly<{ value: string | null }>) {
  if (!value || Number.isNaN(Date.parse(value))) return <span className="muted-text">{formatTimestamp(null)}</span>;
  return <time dateTime={value}>{formatTimestamp(value)}</time>;
}
