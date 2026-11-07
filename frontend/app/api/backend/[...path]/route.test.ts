// @vitest-environment node
import { beforeEach, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { DELETE, POST } from "./route";
beforeEach(()=>{vi.restoreAllMocks();vi.stubEnv("BACKEND_INTERNAL_URL","http://127.0.0.1:8000");vi.stubEnv("FLOWMEDIC_API_KEY","server-only-test-token");});
it("forwards delete revision, selected cookies and CSRF without client authority",async()=>{
  const fetch=vi.spyOn(globalThis,"fetch").mockResolvedValue(new Response('{"deleted":true}',{status:200,headers:{"Set-Cookie":"fm_csrf=new; Path=/"}}));
  const request=new NextRequest("http://localhost:3000/api/backend/api/v1/settings/n8n-instances/id",{method:"DELETE",headers:{"If-Match":'"3"',"Cookie":"fm_session=session; fm_csrf=csrf; unrelated=secret","X-CSRF-Token":"csrf","Origin":"http://localhost:3000","Authorization":"Bearer client-token","X-Workspace-ID":"foreign"}});
  const response=await DELETE(request,{params:Promise.resolve({path:["api","v1","settings","n8n-instances","id"]})});
  const options=fetch.mock.calls[0][1]!;
  const headers=new Headers(options.headers);
  expect(headers.get("if-match")).toBe('"3"');
  expect(headers.get("cookie")).toBe("fm_session=session; fm_csrf=csrf");
  expect(headers.get("x-csrf-token")).toBe("csrf");
  expect(headers.get("origin")).toBe("http://localhost:3000");
  expect(headers.get("authorization")).toBe("Bearer server-only-test-token");
  expect(headers.has("x-workspace-id")).toBe(false);expect(headers.has("host")).toBe(false);
  expect(options).toMatchObject({method:"DELETE",body:"",cache:"no-store",redirect:"manual"});
  expect(response.headers.get("cache-control")).toBe("no-store");
  expect(response.headers.get("set-cookie")).toContain("fm_csrf=new");
  expect(await response.json()).toEqual({deleted:true});
  expect([...response.headers.values()].join()).not.toContain("server-only-test-token");
});
it("preserves mutation bodies and passes through safe validation failures",async()=>{
  const fetch=vi.spyOn(globalThis,"fetch").mockResolvedValue(new Response('{"error":{"code":"connection_changed","message":"Reload"}}',{status:409}));
  const request=new NextRequest("http://localhost:3000/api/backend/api/v1/settings/ai-providers",{method:"POST",headers:{"Content-Type":"application/json"},body:'{"name":"Test"}'});
  const response=await POST(request,{params:Promise.resolve({path:["api","v1","settings","ai-providers"]})});
  expect(fetch.mock.calls[0][1]?.body).toBe('{"name":"Test"}');expect(response.status).toBe(409);
  expect(await response.json()).toMatchObject({error:{code:"connection_changed"}});
});
