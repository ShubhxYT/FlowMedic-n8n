import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import SettingsPage from "./page";
import { api } from "@/lib/api";
import { savedConnection } from "@/tests/connections";
const { scope }=vi.hoisted(()=>({scope:{workspaceId:"w",instanceId:null as string|null,role:"admin",instances:[] as typeof savedConnection[]}}));
vi.mock("@/components/connection-scope",()=>({useConnectionScope:()=>scope}));
vi.mock("@/components/connection-settings",()=>({ConnectionSettings:()=> <p>Connection controls</p>}));
vi.mock("@/components/workspace-members",()=>({WorkspaceMembers:()=> <p>Workspace members</p>}));
vi.mock("@/lib/api",async original=>({...await original<typeof import("@/lib/api")>(),api:{health:vi.fn(),systemStatus:vi.fn(),monitoringStatus:vi.fn(),monitoringSync:vi.fn(),n8nStatus:vi.fn()}}));
beforeEach(()=>{
  vi.clearAllMocks();scope.instanceId=null;scope.instances=[{...savedConnection,enabled:true}];
  vi.mocked(api.health).mockResolvedValue({status:"ok"});
  vi.mocked(api.systemStatus).mockResolvedValue({database_engine:"sqlite",ai_mode:"mock",n8n_configured:true,enabled_n8n_count:1,poll_interval_seconds:60,retention_days:30});
  vi.mocked(api.monitoringStatus).mockResolvedValue({items:[{workspace_id:"w",instance_id:savedConnection.id,instance_name:"Primary n8n",configuration_state:"ready",enabled:true,state:"idle",poll_interval_seconds:60,last_checked_at:null,last_fresh_poll_at:null,last_successful_sync_at:null,last_error_at:null,last_error_summary:null,consecutive_failure_count:0,backfill_pending:false,backfill_completed_at:null,backfill_state:"complete",lease_state:"unclaimed",retention_days:30,last_retention_at:null}]});
  vi.mocked(api.monitoringSync).mockResolvedValue({status:"completed"});
});
it("reads the managed monitoring items envelope and shows real retention without an unscoped connectivity call",async()=>{
  render(<SettingsPage/>);
  expect(await screen.findByText("30 days")).toBeInTheDocument();
  expect(screen.getByRole("heading",{name:"Primary n8n"})).toBeInTheDocument();
  expect(screen.getByText("idle")).toBeInTheDocument();
  expect(screen.queryByText("Unknown days")).not.toBeInTheDocument();
  expect(api.n8nStatus).not.toHaveBeenCalled();
  expect(screen.queryByText("Sync now")).not.toBeInTheDocument();
});
it("syncs only an explicitly selected enabled instance",async()=>{
  scope.instanceId=savedConnection.id;
  render(<SettingsPage/>);await screen.findByText("30 days");
  fireEvent.click(screen.getByText("Sync now"));
  await waitFor(()=>expect(api.monitoringSync).toHaveBeenCalledWith(savedConnection.id));
});
