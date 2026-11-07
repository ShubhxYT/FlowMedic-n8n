import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionForm } from "@/components/connection-form";
import { savedConnection } from "@/tests/connections";

function editor(overrides: Partial<React.ComponentProps<typeof ConnectionForm>> = {}) {
  const props = {family: "n8n" as const, saved: savedConnection, stateRevision: 7,
    pending: false, onSave: vi.fn().mockResolvedValue(true), onTest: vi.fn().mockResolvedValue(undefined), onClose: vi.fn(), ...overrides};
  render(<ConnectionForm {...props} />);
  return props;
}
function replaceKey() {
  fireEvent.change(screen.getByLabelText("API key action"), {target: {value: "replace"}});
  fireEvent.change(screen.getByLabelText("New API key"), {target: {value: "transient-test-key"}});
}
beforeEach(() => { vi.spyOn(HTMLFormElement.prototype, "reportValidity").mockReturnValue(true); });

describe("ConnectionForm secret semantics", () => {
  it("never fills a stored key and omits it when keeping it", async () => {
    const props = editor();
    expect(screen.queryByLabelText("New API key")).not.toBeInTheDocument();
    await act(async () => {fireEvent.submit(screen.getByRole("form"));});
    await waitFor(() => expect(props.onSave).toHaveBeenCalled());
    expect(vi.mocked(props.onSave).mock.calls[0][0]).not.toHaveProperty("api_key");
  });
  it("sends an entered replacement only in the candidate and clears it after success", async () => {
    const props = editor(); replaceKey();
    expect(screen.getByLabelText("New API key")).toHaveAttribute("type", "password");
    await act(async () => {fireEvent.submit(screen.getByRole("form"));});
    await waitFor(() => expect(screen.getByLabelText("New API key")).toHaveValue(""));
    expect(vi.mocked(props.onSave).mock.calls[0][0].api_key).toBe("transient-test-key");
    expect(localStorage.length).toBe(0);
  });
  it("removal disables the connection and clears replacement text", async () => {
    const props = editor({saved: {...savedConnection, enabled: true}}); replaceKey();
    fireEvent.change(screen.getByLabelText("API key action"), {target: {value: "remove"}});
    await act(async () => {fireEvent.submit(screen.getByRole("form"));});
    expect(props.onSave).toHaveBeenCalledWith(expect.objectContaining({remove_api_key: true, enabled: false}));
    expect(vi.mocked(props.onSave).mock.calls[0][0]).not.toHaveProperty("api_key");
  });
  it("clears the transient key on Cancel", async () => {
    const props = editor(); replaceKey(); fireEvent.click(screen.getByText("Cancel"));
    expect(screen.getByLabelText("New API key")).toHaveValue("");
    expect(props.onClose).toHaveBeenCalledOnce();
  });
  it("saves new connections disabled before allowing explicit enable", async () => {
    const props = editor({saved: undefined});
    expect(screen.getByLabelText(/Enabled/)).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Display name"), {target:{value:"New instance"}});
    fireEvent.change(screen.getByLabelText("Base URL"), {target:{value:"http://127.0.0.1:5678"}});
    fireEvent.change(screen.getByLabelText("New API key"), {target:{value:"test-key"}});
    await act(async () => {fireEvent.submit(screen.getByRole("form"));});
    expect(props.onSave).toHaveBeenCalledWith(expect.objectContaining({enabled:false}));
  });
  it("blocks Test on invalid inputs without saving", async () => {
    const props = editor(); vi.mocked(HTMLFormElement.prototype.reportValidity).mockReturnValue(false);
    fireEvent.click(screen.getByText("Test connection"));
    expect(props.onTest).not.toHaveBeenCalled(); expect(props.onSave).not.toHaveBeenCalled();
  });
  it("sends structured-output acknowledgement only when explicitly selected", async () => {
    const props = editor({family: "ai", saved:{...savedConnection, kind:"openai-compatible", model:"test-model"}});
    fireEvent.click(screen.getByText("Test connection"));
    expect(props.onTest).toHaveBeenLastCalledWith(expect.anything(), "access", false);
    fireEvent.click(screen.getByLabelText(/Test structured output/));
    fireEvent.click(screen.getByText("Test connection"));
    expect(props.onTest).toHaveBeenLastCalledWith(expect.anything(), "structured_output", true);
    fireEvent.change(screen.getByLabelText("Provider type"), {target:{value:"mock"}});
    fireEvent.click(screen.getByText("Test connection"));
    expect(props.onTest).toHaveBeenLastCalledWith(expect.not.objectContaining({api_key:expect.anything()}), "access", false);
    expect(props.onSave).not.toHaveBeenCalled();
  });
  it("requires same-server acknowledgement for a URL edit", async () => {
    const props=editor();
    fireEvent.change(screen.getByLabelText("Base URL"), {target:{value:"http://127.0.0.1:5678/proxy"}});
    fireEvent.click(screen.getByLabelText(/This URL still identifies/));
    await act(async () => {fireEvent.submit(screen.getByRole("form"));});
    expect(props.onSave).toHaveBeenCalledWith(expect.objectContaining({confirm_same_server:true}));
  });
  it("disables editing and actions while a request is pending", async () => {
    editor({pending:true});
    expect(screen.getByLabelText("Display name")).toBeDisabled();
    expect(screen.getByText("Save connection")).toBeDisabled();
    expect(screen.getByText("Cancel")).toBeDisabled();
  });
});
