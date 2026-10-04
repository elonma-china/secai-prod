import { afterEach, expect, it, vi } from "vitest";
import { createReviewApi } from "./reviewApi";

afterEach(() => vi.unstubAllGlobals());

it("uses a server-resolved role without exposing a review token in Devspace", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ actor_id: "review-admin", role: "admin" }) });
  vi.stubGlobal("fetch", fetchMock);
  await createReviewApi("devspace:admin").me();
  const headers = fetchMock.mock.calls[0][1].headers;
  expect(headers["X-Review-Role"]).toBe("admin");
  expect(headers).not.toHaveProperty("Authorization");
});

it("keeps bearer auth for direct API clients", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ actor_id: "reviewer", role: "reviewer" }) });
  vi.stubGlobal("fetch", fetchMock);
  await createReviewApi("separate-api-token").me();
  expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer separate-api-token");
});
