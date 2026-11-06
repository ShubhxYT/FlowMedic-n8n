type StateTone = "empty" | "error" | "loading";

export function StatePanel({ title, message, action, tone = "empty", inline = false }: Readonly<{ title: string; message: string; action?: React.ReactNode; tone?: StateTone; inline?: boolean }>) {
  const classes = ["state-panel", `state-panel-${tone}`, inline ? "state-panel-inline" : "state-panel-standalone"].join(" ");
  return (
    <section className={classes} role={tone === "error" ? "alert" : "status"} aria-busy={tone === "loading" || undefined}>
      <div>
        <h2>{title}</h2>
        <p>{message}</p>
        {tone === "loading" && <div className="state-skeleton" aria-hidden="true"><span className="skeleton" /><span className="skeleton" style={{ width: "82%" }} /><span className="skeleton" style={{ width: "64%" }} /></div>}
        {action && <div className="mt-5">{action}</div>}
      </div>
    </section>
  );
}
