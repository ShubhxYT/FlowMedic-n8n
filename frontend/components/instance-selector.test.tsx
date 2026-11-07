import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { InstanceSelector } from "./instance-selector";
import { savedConnection } from "@/tests/connections";
const { setInstance }=vi.hoisted(()=>({setInstance:vi.fn()}));
vi.mock("@/components/connection-scope",()=>({useConnectionScope:()=>({loading:false,instanceId:null,instances:[savedConnection,{...savedConnection,id:"second-id"}],setInstance})}));
it("keeps all instances selected until the user chooses a disambiguated ID",()=>{
  render(<InstanceSelector/>);
  expect(screen.getByLabelText("n8n instance")).toHaveValue("");
  expect(screen.getByRole("option",{name:/11111111/})).toBeInTheDocument();
  expect(screen.getByRole("option",{name:/second-i/})).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("n8n instance"),{target:{value:"second-id"}});
  expect(setInstance).toHaveBeenCalledWith("second-id");
  fireEvent.change(screen.getByLabelText("n8n instance"),{target:{value:""}});
  expect(setInstance).toHaveBeenLastCalledWith(null);
});
