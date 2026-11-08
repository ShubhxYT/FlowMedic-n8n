"use client";

import { RefreshCw } from "lucide-react";
import { useConnectionScope } from "@/components/connection-scope";
import { InstanceSelector } from "@/components/instance-selector";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { AlertIcon, BellIcon, CloseIcon, GridIcon, LockIcon, MenuIcon, PulseIcon, SettingsIcon, WorkflowIcon } from "@/components/icons";
import { SystemStatus } from "@/components/system-status";
import { useAccount } from "@/components/account-provider";
import { api } from "@/lib/api";

const navigation = [
  { href: "/", label: "Overview", icon: GridIcon },
  { href: "/workflows", label: "Workflows", icon: WorkflowIcon },
  { href: "/incidents", label: "Incidents", icon: AlertIcon },
  { href: "/alerts", label: "Alerts", icon: BellIcon },
  { href: "/settings", label: "Settings", icon: SettingsIcon },
];

type FocusTarget = "toggle" | "main" | null;

export function DashboardShell({ children }: Readonly<{ children: React.ReactNode }>) {
  const pathname = usePathname();
  const account = useAccount();
  const scope = useConnectionScope();
  const [revision, setRevision] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [accountError, setAccountError] = useState("");
  async function accountAction(action: () => Promise<unknown>, destination: string) {
    setAccountError("");
    try { await action(); window.location.assign(destination); }
    catch (reason) { setAccountError(reason instanceof Error ? reason.message : "Account action failed"); }
  }
  async function refreshDashboard() {
    setRefreshing(true);
    try {
      await scope.refresh();
      setRevision((current) => current + 1);
    } finally {
      setRefreshing(false);
    }
  }
  const [openAt, setOpenAt] = useState<string | null>(null);
  const open = openAt === pathname;
  const toggleRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const restoreFocus = useRef<FocusTarget>(null);

  function closeDrawer(target: FocusTarget) {
    restoreFocus.current = target;
    setOpenAt(null);
  }

  useEffect(() => {
    if (open) {
      closeRef.current?.focus();
      return;
    }
    const target = restoreFocus.current === "main" ? mainRef.current : restoreFocus.current === "toggle" ? toggleRef.current : null;
    restoreFocus.current = null;
    target?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") closeDrawer("toggle");
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  return (
    <div className="dashboard shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <header className="toolbar" inert={open}>
        <button
          ref={toggleRef}
          type="button"
          className="icon-button lg:hidden"
          onClick={() => setOpenAt(pathname)}
          aria-label="Open navigation"
          aria-expanded={open}
          aria-controls="primary-sidebar"
        >
          <MenuIcon className="size-5" />
        </button>
        <div className="toolbar-actions">
          {account && !account.demo && <>
            <label className="instance-picker"><span>Workspace</span><select aria-label="Workspace" value={account.workspace.id}
              onChange={(event) => void accountAction(() => api.selectWorkspace(event.target.value), "/")}>
              {account.workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name}</option>)}
            </select></label>
            <button className="button button-secondary" onClick={() => void accountAction(api.logout, "/login")}>Log out</button>
          </>}
          <InstanceSelector />
          <SystemStatus />
          <button className="button" disabled={refreshing} onClick={() => void refreshDashboard()}><RefreshCw size={15} aria-hidden="true" /> Refresh</button>
          {accountError && <span role="alert">{accountError}</span>}
        </div>
      </header>
      <aside id="primary-sidebar" className={`sidebar ${open ? "sidebar-open" : ""}`}>
        <div className="sidebar-head">
          <Link href="/" className="brand" onClick={() => closeDrawer("main")}>
            <span className="brand-mark"><PulseIcon className="size-5" /></span>
            <span>FlowMedic <em>AI</em><small>Automation observability</small></span>
          </Link>
          <button ref={closeRef} type="button" className="icon-button lg:hidden" onClick={() => closeDrawer("toggle")} aria-label="Close navigation">
            <CloseIcon className="size-5" />
          </button>
        </div>
        <p className="sidebar-caption" id="primary-nav-label">Workspace</p>
        <nav aria-labelledby="primary-nav-label">
          {navigation.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link key={href} href={href} aria-current={active ? "page" : undefined} onClick={() => closeDrawer("main")}>
                <Icon className="size-[18px]" />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className="readonly"><LockIcon className="size-4" /><div><strong>Read-only access to n8n</strong><small>Observe and investigate workflows.</small></div></div>
          {account && <div className="workspace"><span className="workspace-avatar" aria-hidden="true">{account.workspace.name.slice(0, 2).toUpperCase()}</span>
            <div><strong>{account.workspace.name}</strong><small>{account.user.email}</small></div></div>}
        </div>
      </aside>
      {open && <button type="button" tabIndex={-1} aria-hidden="true" className="sidebar-scrim lg:hidden" onClick={() => closeDrawer("toggle")} />}
      <main ref={mainRef} id="main-content" tabIndex={-1} className="dashboard-main" inert={open}><div className="content page" key={revision}>{children}</div></main>
    </div>
  );
}
