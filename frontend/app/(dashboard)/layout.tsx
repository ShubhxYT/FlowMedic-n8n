import { ConnectionScopeProvider } from "@/components/connection-scope";
import { DashboardShell } from "@/components/dashboard-shell";
import { AccountProvider } from "@/components/account-provider";
import { requireCurrentUser } from "@/lib/auth-server";

export const dynamic = "force-dynamic";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const account = await requireCurrentUser();
  return <AccountProvider account={account} key={`${account.user.id}:${account.workspace.id}`}>
    <ConnectionScopeProvider key={account.workspace.id} workspaceId={account.workspace.id} role={account.demo ? "viewer" : account.workspace.role}><DashboardShell>{children}</DashboardShell></ConnectionScopeProvider>
  </AccountProvider>;
}
