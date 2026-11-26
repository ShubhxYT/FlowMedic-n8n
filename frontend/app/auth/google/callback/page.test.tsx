import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import GoogleCallbackPage from "@/app/auth/google/callback/page";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { csrf: vi.fn(), googleCallback: vi.fn() } }));

afterEach(() => { vi.clearAllMocks(); window.history.replaceState(null, "", "/"); });

describe("Google callback", () => {
  it("posts the code and state once and strips them from the URL", async () => {
    vi.mocked(api.csrf).mockResolvedValue({ registration_enabled: true });
    vi.mocked(api.googleCallback).mockRejectedValue(new Error("Google sign-in failed"));
    window.history.replaceState(null, "", "/auth/google/callback?code=abc&state=xyz");
    render(<GoogleCallbackPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Google sign-in failed");
    expect(api.googleCallback).toHaveBeenCalledExactlyOnceWith("abc", "xyz");
    expect(window.location.search).toBe("");
  });
  it("shows a cancellation message without calling the API", async () => {
    window.history.replaceState(null, "", "/auth/google/callback?error=access_denied");
    render(<GoogleCallbackPage />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("cancelled"));
    expect(api.googleCallback).not.toHaveBeenCalled();
  });
});
