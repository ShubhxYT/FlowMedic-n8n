"use client";

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
  const [accountError, setAccountError] = useState("");
  async function accountAction(action: () => Promise<unknown>, destination: string) {
    setAccountError("");
    try { await action(); window.location.assign(destination); }
    catch (reason) { setAccountError(reason instanceof Error ? reason.message : "Account action failed"); }
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
    <div className="min-h-screen">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <header className="topbar" inert={open}>
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
      <aside id="primary-sidebar" className={`sidebar ${open ? "sidebar-open" : ""}`}>
        <div className="sidebar-head">
          <Link href="/" className="brand" onClick={() => closeDrawer("main")}>
            <span className="brand-mark"><PulseIcon className="size-5" /></span>
            <span>FlowMedic <em>AI</em></span>
          </Link>
          <button ref={closeRef} type="button" className="icon-button lg:hidden" onClick={() => closeDrawer("toggle")} aria-label="Close navigation">
            <CloseIcon className="size-5" />
          </button>
        </div>
        <p className="nav-label" id="primary-nav-label">Workspace</p>
        <nav aria-labelledby="primary-nav-label" className="space-y-1">
          {navigation.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link key={href} href={href} className={`nav-link ${active ? "nav-link-active" : ""}`} aria-current={active ? "page" : undefined} onClick={() => closeDrawer("main")}>
                <Icon className="size-[18px]" />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <LockIcon className="mt-0.5 size-4 flex-none" />
          <div><strong>Read-only access</strong><small>FlowMedic observes n8n and never applies a fix.</small></div>
        </div>
      </aside>
      {open && <button type="button" tabIndex={-1} aria-hidden="true" className="sidebar-scrim lg:hidden" onClick={() => closeDrawer("toggle")} />}
      <main ref={mainRef} id="main-content" tabIndex={-1} className="dashboard-main" inert={open}><div className="content">{children}</div></main>
    </div>
  );
}
