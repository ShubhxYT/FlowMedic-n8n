import { act, renderHook, waitFor } from "@testing-library/react";
import { expect, it } from "vitest";
import { useApi } from "./use-api";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return {promise, resolve};
}

it("does not let a late response replace the newly selected incident", async () => {
  const first = deferred<string>();
  const second = deferred<string>();
  const {result, rerender} = renderHook(({id}) => useApi(() => id === "first" ? first.promise : second.promise, id), {initialProps: {id: "first"}});
  await act(async () => { await Promise.resolve(); });
  rerender({id: "second"});
  await act(async () => { second.resolve("second incident"); });
  await waitFor(() => expect(result.current.data).toBe("second incident"));
  await act(async () => { first.resolve("first incident"); });
  expect(result.current.data).toBe("second incident");
});

it("hides previous incident data as soon as the route changes", async () => {
  const pending = deferred<string>();
  const {result, rerender} = renderHook(({id}) => useApi(() => id === "first" ? Promise.resolve("old incident") : pending.promise, id), {initialProps: {id: "first"}});
  await waitFor(() => expect(result.current.data).toBe("old incident"));
  rerender({id: "second"});
  expect(result.current.data).toBeNull();
  expect(result.current.loading).toBe(true);
  await act(async () => { pending.resolve("new incident"); });
});
