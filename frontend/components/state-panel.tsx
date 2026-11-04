export function StatePanel({ title, message, action }: Readonly<{ title: string; message: string; action?: React.ReactNode }>) {
  return <section className="state-panel" role="status"><h2>{title}</h2><p>{message}</p>{action && <div className="mt-5">{action}</div>}</section>;
}
