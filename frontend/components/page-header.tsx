export function PageHeader({ title, description, action }: Readonly<{ title: string; description: string; action?: React.ReactNode }>) {
  return <div className="mb-8 flex flex-col justify-between gap-4 sm:flex-row sm:items-end"><div><p className="eyebrow">FlowMedic observability</p><h1>{title}</h1><p className="page-description">{description}</p></div>{action}</div>;
}
