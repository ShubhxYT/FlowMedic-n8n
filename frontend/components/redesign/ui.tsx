import { AlertCircle, ArrowDown, ArrowUpRight, CircleCheck, Clock3, RefreshCw, Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { ManagedConnection } from "@/lib/types";

export interface PageProps {
  instance: string | null;
  connections: ManagedConnection[];
}

export const display = (value: string) => value.replaceAll("_", " ").replace(/^./, (character) => character.toUpperCase());
export const dateNumber = (value: string | null) => value ? Date.parse(value) || 0 : 0;
export const time = (value: string | null, includeDate = true, includeZone = true) => {
  if (!value || !dateNumber(value)) return "Unavailable";
  return new Intl.DateTimeFormat(undefined, {
    ...(includeDate ? { month: "short", day: "numeric" } : {}),
    hour: "numeric", minute: "2-digit", ...(includeZone ? { timeZoneName: "short" } : {}),
  }).format(new Date(value));
};
export const number = (value: number) => new Intl.NumberFormat().format(value);

export function useResource<T>(load: () => Promise<T>): { data?: T; error?: string; loading: boolean } {
  const [state, setState] = useState<{ source: () => Promise<T>; data?: T; error?: string }>();
  useEffect(() => {
    let current = true;
    void load().then(
      (data) => { if (current) setState({ source: load, data }); },
      (error: Error) => { if (current) setState({ source: load, error: error.message }); },
    );
    return () => { current = false; };
  }, [load]);
  return state?.source === load ? { ...state, loading: false } : { loading: true };
}

export function usePages<T extends { id: string }>(load: (offset: number) => Promise<T[]>, pageSize: number) {
  const [rows, setRows] = useState<T[]>([]);
  const [offset, setOffset] = useState(0);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => {
    const token = ++generation.current;
    void Promise.resolve().then(() => {
      if (token !== generation.current) return [];
      setRows([]);
      setOffset(0);
      setMore(false);
      setLoading(true);
      setError("");
      return load(0);
    }).then((page) => {
      if (token !== generation.current) return;
      setRows(page);
      setOffset(page.length);
      setMore(page.length === pageSize);
    }, (error: Error) => {
      if (token === generation.current) setError(error.message);
    }).finally(() => {
      if (token === generation.current) setLoading(false);
    });
    return () => { generation.current = token + 1; };
  }, [load, pageSize]);

  async function loadMore() {
    if (loading || !more) return;
    const token = generation.current;
    setLoading(true);
    setError("");
    try {
      const page = await load(offset);
      if (token !== generation.current) return;
      setRows((previous) => [...new Map([...previous, ...page].map((item) => [item.id, item])).values()]);
      setOffset(offset + page.length);
      setMore(page.length === pageSize);
    } catch (error) {
      if (token === generation.current) setError((error as Error).message);
    } finally {
      if (token === generation.current) setLoading(false);
    }
  }
  return { rows, more, loading, error, loadMore };
}

export function Heading({ kicker, title, description, aside }: {
  kicker: string; title: string; description: string; aside?: ReactNode;
}) {
  return <header className="page-heading">
    <div>
      <div className="eyebrow"><span />{kicker}</div>
      <h1>{title}</h1>
      <p>{description}</p>
    </div>
    {aside}
  </header>;
}

export function Panel({ title, description, action, children }: {
  title: string; description?: string; action?: ReactNode; children: ReactNode;
}) {
  return <section className="panel">
    <header className="panel-header">
      <div><h2>{title}</h2>{description && <p>{description}</p>}</div>
      {action}
    </header>
    {children}
  </section>;
}

const colors: Record<string, string> = {
  healthy: "good", enabled: "good", active: "good", delivered: "good", operational: "good", resolved: "good",
  unhealthy: "danger", high: "danger", failed: "danger", open: "danger",
  degraded: "warn", medium: "warn", pending: "warn",
  diagnosed: "accent", running: "accent", idle: "good", configured: "accent",
};
export function Badge({ value, label }: { value: string; label?: string }) {
  return <span className={"badge " + (colors[value] ?? "neutral")}><i />{label ?? display(value)}</span>;
}

export function Notice({ kind = "empty", title, children }: {
  kind?: "empty" | "error" | "loading"; title: string; children?: ReactNode;
}) {
  const Icon = kind === "error" ? AlertCircle : kind === "loading" ? RefreshCw : CircleCheck;
  return <div className={"notice " + kind} role={kind === "error" ? "alert" : "status"}>
    <Icon size={17} aria-hidden="true" />
    <div><strong>{title}</strong>{children && <p>{children}</p>}</div>
  </div>;
}

export function SearchInput({ label, value, onChange, placeholder }: {
  label: string; value: string; onChange: (value: string) => void; placeholder: string;
}) {
  return <label className="search-field">
    <Search size={17} aria-hidden="true" />
    <input type="search" aria-label={label} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
  </label>;
}

export function PageFooter({ count, more, loading, loadMore }: {
  count: number; more: boolean; loading: boolean; loadMore: () => void;
}) {
  return <div className="panel-footer">
    <span>{number(count)} loaded</span>
    {more && <button className="button" disabled={loading} onClick={loadMore}>
      {loading ? "Loading…" : "Load more"}<ArrowDown size={15} aria-hidden="true" />
    </button>}
  </div>;
}

export function Detail({ title, subtitle, onClose, children }: {
  title: string; subtitle: string; onClose: () => void; children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const trigger = document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, []);
  return <dialog ref={ref} className="detail" aria-labelledby="detail-title"
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="detail-inner">
      <header className="detail-header">
        <div><div className="eyebrow"><span />{subtitle}</div><h2 id="detail-title">{title}</h2></div>
        <button className="button icon-button" type="button" autoFocus aria-label="Close details" onClick={onClose}><X size={20} /></button>
      </header>
      {children}
    </div>
  </dialog>;
}

export function Meta({ children }: { children: ReactNode }) {
  return <span className="meta">{children}</span>;
}
export function Outbound({ href, children }: { href: string; children: ReactNode }) {
  return <a className="text-link" href={href} target="_blank" rel="noreferrer">{children}<ArrowUpRight size={15} aria-hidden="true" /></a>;
}
export function Timestamp({ value }: { value: string | null }) {
  return <span className="timestamp"><Clock3 size={13} aria-hidden="true" />{time(value)}</span>;
}
