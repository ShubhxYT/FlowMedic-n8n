import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ConnectionScopeProvider, useConnectionScope } from "@/components/connection-scope";
import { InstanceSelector } from "@/components/instance-selector";
import { connectionApi } from "@/lib/api";
import { savedConnection } from "@/tests/connections";
vi.mock("@/lib/api", async importOriginal => ({...await importOriginal<typeof import("@/lib/api")>(), connectionApi:{list:vi.fn()}}));
function Content(){const scope=useConnectionScope(); return <><InstanceSelector/><p>{scope.workspaceId}:{scope.instanceId ?? "all"}</p></>;}
beforeEach(()=>{vi.clearAllMocks();localStorage.clear();vi.mocked(connectionApi.list).mockResolvedValue({items:[savedConnection],state_revision:1});});
it("persists only a workspace-scoped instance ID and clears a removed selection", async()=>{
  render(<ConnectionScopeProvider workspaceId="w" role="admin"><Content/></ConnectionScopeProvider>);
  await screen.findByText(/Primary n8n/);
  fireEvent.change(screen.getByLabelText("n8n instance"),{target:{value:savedConnection.id}});
  expect(screen.getByText(`w:${savedConnection.id}`)).toBeInTheDocument();
  expect(localStorage.getItem("flowmedic:instance:w")).toBe(savedConnection.id);
  expect(localStorage.length).toBe(1);
});
it("clears a selected ID which no longer exists", async()=>{
  localStorage.setItem("flowmedic:instance:w","deleted-instance");
  render(<ConnectionScopeProvider workspaceId="w" role="admin"><Content/></ConnectionScopeProvider>);
  await waitFor(()=>expect(localStorage.getItem("flowmedic:instance:w")).toBeNull());
  expect(screen.getByText("w:all")).toBeInTheDocument();
});
it("ignores a previous workspace response when switching during a request", async()=>{
  let resolve!: (value: {items:typeof savedConnection[];state_revision:number})=>void;
  vi.mocked(connectionApi.list).mockImplementationOnce(()=>new Promise(done=>{resolve=done;})).mockResolvedValueOnce({items:[{...savedConnection,name:"Workspace B"}],state_revision:1});
  const view=render(<ConnectionScopeProvider key="a" workspaceId="a" role="admin"><Content/></ConnectionScopeProvider>);
  await waitFor(()=>expect(connectionApi.list).toHaveBeenCalledOnce());
  view.rerender(<ConnectionScopeProvider key="b" workspaceId="b" role="viewer"><Content/></ConnectionScopeProvider>);
  await screen.findByText(/Workspace B/);
  await act(async()=>resolve({items:[savedConnection],state_revision:1}));
  expect(screen.queryByText(/Primary n8n/)).not.toBeInTheDocument();
  expect(screen.getByText("b:all")).toBeInTheDocument();
});
