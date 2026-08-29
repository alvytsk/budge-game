import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { withQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import {
  useAddImage,
  useCategories,
  useCategory,
  useReadiness,
  useUploadMedia,
} from "./use-library";

const ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const CATEGORY = {
  id: ID,
  title: "Кино",
  is_secret: false,
  is_active: true,
  version: 3,
  active_image_count: 12,
};

describe("useCategories", () => {
  it("returns inactive categories too", async () => {
    // §5.3: content is soft-deleted. An operator who switched a theme off
    // has to see it to switch it back on. Kills on: filtering here.
    server.use(
      http.get("/api/library/categories", () =>
        HttpResponse.json([CATEGORY, { ...CATEGORY, id: "b", title: "Спорт", is_active: false }]),
      ),
    );
    const { result } = renderHook(() => useCategories(), { wrapper: withQuery() });
    await waitFor(() => expect(result.current.data).toHaveLength(2));
    expect(result.current.data?.map((row) => row.title)).toEqual(["Кино", "Спорт"]);
  });
});

describe("useReadiness", () => {
  it("asks about the board it was given, not a default", async () => {
    // §8's soft check is answered per board size; asking about the wrong
    // number tells the operator they are ready when they are not.
    const asked: string[] = [];
    server.use(
      http.get("/api/library/readiness", ({ request }) => {
        asked.push(new URL(request.url).searchParams.get("cells") ?? "");
        return HttpResponse.json({
          cells: 12,
          threshold: 5,
          ordinary_available: 40,
          secrets_available: 6,
          thin: [],
          ready: true,
        });
      }),
    );
    renderHook(() => useReadiness(12), { wrapper: withQuery() });
    await waitFor(() => expect(asked).toEqual(["12"]));
  });
});

describe("useUploadMedia", () => {
  it("posts the bytes and hands back the digest the server computed", async () => {
    // §7.6 and H9: the digest is the join between the two calls, and the
    // console never invents one.
    server.use(
      http.post("/api/media", () =>
        HttpResponse.json(
          { media_sha256: "c".repeat(64), content_type: "image/png", bytes: 4 },
          { status: 201 },
        ),
      ),
    );
    const { result } = renderHook(() => useUploadMedia(), { wrapper: withQuery() });
    let digest = "";
    await act(async () => {
      const file = new File([new Uint8Array([1, 2, 3, 4])], "x.png", { type: "image/png" });
      digest = (await result.current(file)).media_sha256;
    });
    expect(digest).toBe("c".repeat(64));
  });
});

describe("useAddImage", () => {
  it("refetches the category it changed", async () => {
    // Kills on: a mutation that does not invalidate — the operator adds a
    // picture, the list does not change, so they add it again.
    let reads = 0;
    server.use(
      http.get(`/api/library/categories/${ID}`, () => {
        reads += 1;
        return HttpResponse.json({ category: CATEGORY, images: [] });
      }),
      http.post(`/api/library/categories/${ID}/images`, () =>
        HttpResponse.json(
          {
            id: "i1",
            media_sha256: "c".repeat(64),
            answer_text: "Титаник",
            position: 0,
            is_active: true,
          },
          { status: 201 },
        ),
      ),
    );
    const { result } = renderHook(() => ({ add: useAddImage(), detail: useCategory(ID) }), {
      wrapper: withQuery(),
    });
    await waitFor(() => expect(reads).toBe(1));
    await act(async () => {
      await result.current.add.mutateAsync({
        categoryId: ID,
        media_sha256: "c".repeat(64),
        answer_text: "Титаник",
      });
    });
    await waitFor(() => expect(reads).toBe(2));
  });
});
