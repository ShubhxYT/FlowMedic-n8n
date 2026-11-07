import { DashboardShell } from "@/components/dashboard-shell";
import { AccountProvider } from "@/components/account-provider";
import { requireCurrentUser } from "@/lib/auth-server";

export const dynamic = "force-dynamic";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const account = await requireCurrentUser();
  return <AccountProvider account={account} key={`${account.user.id}:${account.workspace.id}`}>
    <DashboardShell>{children}</DashboardShell>
  </AccountProvider>;
}
