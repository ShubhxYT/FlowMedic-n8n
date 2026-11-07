import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/incidents" }));
vi.mock("@/lib/api", () => ({ api: { health: vi.fn().mockResolvedValue({ status: "ok" }) } }));
vi.mock("@/components/connection-scope", () => ({ useConnectionScope: () => ({ workspaceId: "w", role: "admin", instanceId: null, instances: [], loading: false, setInstance: vi.fn(), refresh: vi.fn() }) }));

import { DashboardShell } from "@/components/dashboard-shell";

describe("DashboardShell", () => {
  it("marks the current page and exposes a skip link", async () => {
    render(<DashboardShell><p>content</p></DashboardShell>);
    await screen.findByText("Backend online");
    expect(screen.getByRole("link", { name: "Incidents" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main-content");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });

  it("opens the mobile drawer and closes it with Escape, returning focus to the toggle", async () => {
    render(<DashboardShell><p>content</p></DashboardShell>);
    await screen.findByText("Backend online");
    const toggle = screen.getByRole("button", { name: "Open navigation" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById("primary-sidebar")).toHaveClass("sidebar-open");
    expect(screen.getByRole("button", { name: "Close navigation" })).toHaveFocus();
    expect(document.getElementById("main-content")).toHaveAttribute("inert");

    fireEvent.keyDown(document, { key: "Escape" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(document.getElementById("primary-sidebar")).not.toHaveClass("sidebar-open");
    expect(toggle).toHaveFocus();
  });

  it("moves focus to the main content after navigating from the drawer", async () => {
    render(<DashboardShell><p>content</p></DashboardShell>);
    await screen.findByText("Backend online");
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    const block = (event: Event) => event.preventDefault();
    document.addEventListener("click", block);
    fireEvent.click(screen.getByRole("link", { name: "Alerts" }));
    document.removeEventListener("click", block);
    expect(document.getElementById("primary-sidebar")).not.toHaveClass("sidebar-open");
    expect(document.getElementById("main-content")).not.toHaveAttribute("inert");
    expect(screen.getByRole("main")).toHaveFocus();
  });
});
