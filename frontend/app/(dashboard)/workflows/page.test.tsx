import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { savedConnection } from "@/tests/connections";
import WorkflowsPage from "./page";
const { scope } = vi.hoisted(()=>({scope:{workspaceId:"w",instanceId:null as string|null,instances:[] as typeof savedConnection[]}}));
vi.mock("@/components/connection-scope",()=>({useConnectionScope:()=>scope}));
vi.mock("@/lib/api",async original=>({...await original<typeof import("@/lib/api")>(),api:{workflowMetrics:vi.fn(),workflows:vi.fn()}}));
beforeEach(()=>{
  vi.clearAllMocks(); scope.instanceId=null;
  scope.instances=[{...savedConnection,name:"First",enabled:true},{...savedConnection,id:"second-id",name:"Second",enabled:true,base_url:"http://127.0.0.1:5679"}];
  vi.mocked(api.workflowMetrics).mockResolvedValue({data:scope.instances.map(item=>({workspace_id:"w",instance_id:item.id,workflow_id:"shared-id",workflow_name:"Orders",health:"healthy",last_checked_at:null,last_successful_execution_at:null,last_failed_execution_at:null,recent_executions_count:1,recent_success_count:1,recent_failure_count:0,success_rate:1,open_incident_count:0}))});
  vi.mocked(api.workflows).mockImplementation(async id=>({data:[{workspace_id:"w",instance_id:id,id:"shared-id",name:"Live orders",active:true}],next_cursor:null}));
});
it("keeps reused workflow IDs as separate labelled rows and requires selection for live data",async()=>{
  const label=(text:string)=>(_:string,element:Element|null)=>element?.tagName==="SPAN"&&element.classList.contains("subtle")&&element.textContent===text;
  render(<WorkflowsPage/>);await screen.findByText(label("First · shared-id · 1 recent executions"));
  expect(screen.getByText(label("Second · shared-id · 1 recent executions"))).toBeInTheDocument();
  expect(screen.getAllByRole("row")).toHaveLength(3);
  expect(screen.getByText("Select an n8n instance")).toBeInTheDocument();
  expect(api.workflows).not.toHaveBeenCalled();
});
it("uses the selected instance for live requests and same-instance workflow links",async()=>{
  scope.instanceId=savedConnection.id;
  const view=render(<WorkflowsPage/>);
  expect(await screen.findByRole("link",{name:/Live orders/})).toHaveAttribute("href","http://127.0.0.1:5678/workflow/shared-id");
  expect(api.workflows).toHaveBeenLastCalledWith(savedConnection.id);
  scope.instanceId="second-id";view.rerender(<WorkflowsPage/>);
  await waitFor(()=>expect(screen.getByRole("link",{name:/Live orders/})).toHaveAttribute("href","http://127.0.0.1:5679/workflow/shared-id"));
  expect(api.workflows).toHaveBeenLastCalledWith("second-id");
  expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(3);
});
