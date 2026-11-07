"use client";

import { InstanceSelector } from "@/components/instance-selector";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { AlertIcon, GridIcon, MenuIcon, PulseIcon, SettingsIcon, WorkflowIcon } from "@/components/icons";
import { SystemStatus } from "@/components/system-status";
import { useAccount } from "@/components/account-provider";
import { api } from "@/lib/api";

const navigation = [
  { href: "/", label: "Overview", icon: GridIcon },
  { href: "/workflows", label: "Workflows", icon: WorkflowIcon },
  { href: "/incidents", label: "Incidents", icon: AlertIcon },
  { href: "/alerts", label: "Alerts", icon: AlertIcon },
  { href: "/settings", label: "Settings", icon: SettingsIcon },
];

export function DashboardShell({ children }: Readonly<{ children: React.ReactNode }>) {
  const pathname = usePathname();
  const account = useAccount();
  const [accountError, setAccountError] = useState("");
  async function accountAction(action: () => Promise<unknown>, destination: string) {
    setAccountError("");
    try { await action(); window.location.assign(destination); }
    catch (reason) { setAccountError(reason instanceof Error ? reason.message : "Account action failed"); }
  }
  const [open, setOpen] = useState(false);

  return (
    <div className="min-h-screen bg-[var(--canvas)] text-slate-100">
      <header className="topbar">
        <button className="icon-button lg:hidden" onClick={() => setOpen(!open)} aria-label="Toggle navigation">
          <MenuIcon className="size-5" />
        </button>
        <div className="ml-auto flex flex-wrap items-center gap-3">
          <span>{account?.user.email} · {account?.workspace.role}</span>
          {account && !account.demo && <>
            <label>Workspace <select aria-label="Workspace" value={account.workspace.id}
              onChange={(event) => void accountAction(() => api.selectWorkspace(event.target.value), "/")}>
              {account.workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
            </select></label>
            <button className="button button-secondary" onClick={() => void accountAction(api.logout, "/login")}>Log out</button>
          </>}
          <InstanceSelector />
          <SystemStatus />
          {accountError && <span role="alert">{accountError}</span>}
        </div>
      </header>
      <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
        <Link href="/" className="brand" onClick={() => setOpen(false)}>
          <span className="brand-mark"><PulseIcon className="size-5" /></span>
          <span>FlowMedic <em>AI</em></span>
        </Link>
        <nav aria-label="Primary navigation" className="mt-10 space-y-1">
          {navigation.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link key={href} href={href} className={`nav-link ${active ? "nav-link-active" : ""}`} onClick={() => setOpen(false)}>
                <Icon className="size-[18px]" />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <span className="status-dot" />
          <div><strong>Backend status</strong><small>Live state in header</small></div>
        </div>
      </aside>
      {open && <button className="sidebar-scrim lg:hidden" onClick={() => setOpen(false)} aria-label="Close navigation" />}
      <main className="dashboard-main">{children}</main>
    </div>
  );
}
