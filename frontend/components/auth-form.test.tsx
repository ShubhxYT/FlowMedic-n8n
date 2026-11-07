import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthForm } from "@/components/auth-form";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { csrf: vi.fn(), login: vi.fn(), signup: vi.fn() } }));

beforeEach(() => {
  vi.mocked(api.csrf).mockResolvedValue({ registration_enabled: true });
});

describe("account forms", () => {
  it("labels fields, enables submission after CSRF bootstrap, and displays safe login failure", async () => {
    vi.mocked(api.login).mockRejectedValue(new Error("Email or password is incorrect"));
    render(<AuthForm mode="login" />);
    const button = screen.getByRole("button", { name: "Log in" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "person@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "long-password-123" } });
    fireEvent.click(button);
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect");
  });
  it("disables signup when registration is closed", async () => {
    vi.mocked(api.csrf).mockResolvedValue({ registration_enabled: false });
    render(<AuthForm mode="signup" />);
    expect(await screen.findByRole("status")).toHaveTextContent("Registration is closed");
    expect(screen.getByRole("button", { name: "Create account" })).toBeDisabled();
  });
});
