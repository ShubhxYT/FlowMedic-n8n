export function PageHeader({ title, description, eyebrow, action }: Readonly<{ title: string; description: string; eyebrow?: string; action?: React.ReactNode }>) {
  return <header className="page-header"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1><p className="page-description">{description}</p></div>{action && <div className="flex-none">{action}</div>}</header>;
}
