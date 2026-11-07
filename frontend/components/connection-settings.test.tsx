import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ConnectionSettings } from "@/components/connection-settings";
import { ConnectionScopeProvider } from "@/components/connection-scope";
import { ApiError, connectionApi } from "@/lib/api";
import type { ConnectionCollection, ConnectionPage } from "@/lib/types";
import { savedConnection } from "@/tests/connections";
vi.mock("@/lib/api", async importOriginal => ({...await importOriginal<typeof import("@/lib/api")>(), connectionApi:{list:vi.fn(),save:vi.fn(),remove:vi.fn(),test:vi.fn()}}));
let pages: Record<ConnectionCollection,ConnectionPage>;
function view(workspace="w",role:"admin"|"viewer"="admin") { return <ConnectionScopeProvider key={workspace} workspaceId={workspace} role={role}><ConnectionSettings/></ConnectionScopeProvider>; }
const section=()=>within(screen.getByRole("region",{name:"n8n instances"}));
beforeEach(()=>{
  localStorage.clear(); vi.clearAllMocks();
  vi.spyOn(HTMLFormElement.prototype,"reportValidity").mockReturnValue(true);
  pages={"n8n-instances":{items:[{...savedConnection}],state_revision:4},"ai-providers":{items:[],state_revision:4}};
  vi.mocked(connectionApi.list).mockImplementation(async collection=>pages[collection]);
  vi.mocked(connectionApi.test).mockResolvedValue({ok:true,capability:"workflows_read",tested_revision:3,message:"Access confirmed"});
  vi.mocked(connectionApi.save).mockImplementation(async (collection,values,saved)=>{
    const row={...(saved ?? savedConnection),...values,id:saved?.id ?? "new-id",revision:(saved?.revision??0)+1};
    pages[collection]={...pages[collection],items:[...pages[collection].items.filter(item=>item.id!==row.id),row]};
    return row;
  });
  vi.mocked(connectionApi.remove).mockImplementation(async (collection,saved)=>{pages[collection].items=pages[collection].items.filter(item=>item.id!==saved.id);return {deleted:true};});
});
it("adds, tests without saving, saves disabled, explicitly enables, edits, disables, and deletes",async()=>{
  pages["n8n-instances"].items=[];
  render(view()); await section().findByText("No connections configured.");
  fireEvent.click(section().getByText("Add instance"));
  fireEvent.change(screen.getByLabelText("Display name"),{target:{value:"Lab n8n"}});
  fireEvent.change(screen.getByLabelText("Base URL"),{target:{value:"http://127.0.0.1:5678"}});
  fireEvent.change(screen.getByLabelText("New API key"),{target:{value:"test-key-only"}});
  fireEvent.click(screen.getByText("Test connection"));
  await screen.findByText("workflows_read: Access confirmed",{selector:"dialog p"});
  expect(connectionApi.save).not.toHaveBeenCalled();
  expect(connectionApi.test).toHaveBeenCalledWith("n8n-instances",expect.objectContaining({api_key:"test-key-only",enabled:false}),"access",false,undefined);
  fireEvent.click(screen.getByText("Save connection"));
  await waitFor(()=>expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.queryByDisplayValue("test-key-only")).not.toBeInTheDocument();
  fireEvent.click(section().getByText("Enable")); await section().findByText("Disable");
  fireEvent.click(section().getByText("Edit / test"));
  expect(screen.queryByLabelText("New API key")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Display name"),{target:{value:"Renamed lab"}});
  fireEvent.click(screen.getByText("Save connection"));
  await waitFor(()=>expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  fireEvent.click(section().getByText("Disable")); await section().findByText("Enable");
  fireEvent.click(section().getByText("Delete"));
  expect(screen.getByText(/History will be retained/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button",{name:"Delete connection"}));
  await waitFor(()=>expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(section().getByText("No connections configured.")).toBeInTheDocument();
  expect(connectionApi.remove).toHaveBeenCalledWith("n8n-instances",expect.objectContaining({revision:4}));
});
it("renders duplicate display names with separate identities",async()=>{
  pages["n8n-instances"].items.push({...savedConnection,id:"22222222-2222-4222-8222-222222222222"});
  render(view()); await waitFor(()=>expect(screen.getAllByText("Primary n8n")).toHaveLength(2));
  expect(section().getByText(/22222222/)).toBeInTheDocument();
  expect(section().getByText(/11111111/)).toBeInTheDocument();
});
it("shows viewers metadata with no mutation or test controls",async()=>{
  render(view("w","viewer")); await screen.findByText("Primary n8n");
  for(const name of ["Add instance","Add provider","Edit / test","Enable","Delete"]){expect(screen.queryByText(name)).not.toBeInTheDocument();}
  expect(screen.getByText(savedConnection.base_url!)).toBeInTheDocument();
});
it("handles revision conflicts with reload inside the dialog and no automatic replay",async()=>{
  vi.mocked(connectionApi.save).mockRejectedValueOnce(new ApiError("Conflict",409));
  render(view()); await screen.findByText("Primary n8n");
  fireEvent.click(section().getByText("Edit / test"));
  fireEvent.change(screen.getByLabelText("API key action"),{target:{value:"replace"}});
  fireEvent.change(screen.getByLabelText("New API key"),{target:{value:"discard-on-conflict"}});
  fireEvent.click(screen.getByText("Save connection"));
  const dialog=await screen.findByRole("dialog");
  await within(dialog).findByText(/This connection changed/);
  expect(connectionApi.save).toHaveBeenCalledOnce();
  fireEvent.click(within(dialog).getByText("Reload connections"));
  await waitFor(()=>expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  fireEvent.click(section().getByText("Edit / test"));
  expect(screen.queryByDisplayValue("discard-on-conflict")).not.toBeInTheDocument();
});
it("keeps Escape from closing pending work and drops feedback after a workspace change",async()=>{
  let resolve!: (value: Awaited<ReturnType<typeof connectionApi.test>>)=>void;
  vi.mocked(connectionApi.test).mockImplementationOnce(()=>new Promise(done=>{resolve=done;}));
  const root=render(view()); await screen.findByText("Primary n8n");
  fireEvent.click(section().getByText("Edit / test"));
  fireEvent.click(screen.getByText("Test connection"));
  const dialog=screen.getByRole("dialog");
  fireEvent(dialog,new Event("cancel",{cancelable:true}));
  expect(dialog).toBeInTheDocument();
  pages["n8n-instances"].items=[];
  root.rerender(view("other-workspace"));
  await section().findByText("No connections configured.");
  await act(async()=>resolve({ok:true,capability:"workflows_read",tested_revision:3,message:"Old workspace feedback"}));
  expect(screen.queryByText(/Old workspace feedback/)).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("uses the workspace state revision when selecting an enabled AI default",async()=>{
  pages["ai-providers"].items=[{...savedConnection,kind:"mock",name:"Local diagnosis",base_url:null,enabled:true,has_api_key:false}];
  render(view()); await screen.findByText("Local diagnosis");
  fireEvent.click(screen.getByText("Make default"));
  await waitFor(()=>expect(connectionApi.save).toHaveBeenCalledWith("ai-providers",{make_default:true,expected_state_revision:4},expect.objectContaining({name:"Local diagnosis"})));
});
it("allows Escape to cancel an idle form and unmount its secret state",async()=>{
  render(view()); await screen.findByText("Primary n8n"); fireEvent.click(section().getByText("Edit / test"));
  fireEvent.change(screen.getByLabelText("API key action"),{target:{value:"replace"}});
  fireEvent.change(screen.getByLabelText("New API key"),{target:{value:"cancelled-key"}});
  fireEvent(screen.getByRole("dialog"),new Event("cancel",{cancelable:true}));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  fireEvent.click(section().getByText("Edit / test"));
  expect(screen.queryByDisplayValue("cancelled-key")).not.toBeInTheDocument();
});
