import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { renderWithQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { CategoryEditor } from "./category-editor";

const ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const DETAIL = {
  category: {
    id: ID,
    title: "Кино",
    is_secret: false,
    is_active: true,
    version: 3,
    active_image_count: 2,
  },
  images: [
    {
      id: "i1",
      media_sha256: "a".repeat(64),
      answer_text: "Титаник",
      position: 0,
      is_active: true,
    },
    {
      id: "i2",
      media_sha256: "b".repeat(64),
      answer_text: "Аватар",
      position: 1,
      is_active: false,
    },
  ],
};

describe("CategoryEditor", () => {
  it("shows each image's answer and its picture", async () => {
    server.use(http.get(`/api/library/categories/${ID}`, () => HttpResponse.json(DETAIL)));
    renderWithQuery(<CategoryEditor categoryId={ID} />);
    expect(await screen.findByDisplayValue("Титаник")).toBeInTheDocument();
    expect(screen.getByAltText("Титаник")).toHaveAttribute("src", `/api/media/${"a".repeat(64)}`);
  });

  it("marks a soft-deleted image without hiding it", async () => {
    // §5.3: soft delete has an undo, and an invisible row cannot be undone.
    server.use(http.get(`/api/library/categories/${ID}`, () => HttpResponse.json(DETAIL)));
    renderWithQuery(<CategoryEditor categoryId={ID} />);
    expect(await screen.findByTestId("image-i2")).toHaveAttribute("data-active", "false");
    expect(screen.getByTestId("image-i1")).toHaveAttribute("data-active", "true");
  });

  it("uploads the file first and records the digest it got back", async () => {
    // H9. Kills on: sending the filename, or a digest computed in the
    // browser — the server refuses a digest it has not stored, and the
    // operator would see an upload that silently added nothing.
    const recorded: unknown[] = [];
    server.use(
      http.get(`/api/library/categories/${ID}`, () => HttpResponse.json(DETAIL)),
      http.post("/api/media", () =>
        HttpResponse.json(
          { media_sha256: "c".repeat(64), content_type: "image/png", bytes: 4 },
          { status: 201 },
        ),
      ),
      http.post(`/api/library/categories/${ID}/images`, async ({ request }) => {
        recorded.push(await request.json());
        return HttpResponse.json(
          { id: "i3", media_sha256: "c".repeat(64), answer_text: "", position: 2, is_active: true },
          { status: 201 },
        );
      }),
    );
    renderWithQuery(<CategoryEditor categoryId={ID} />);
    await screen.findByDisplayValue("Титаник");
    await userEvent.upload(
      screen.getByLabelText("Добавить картинку"),
      new File([new Uint8Array([1, 2, 3, 4])], "x.png", { type: "image/png" }),
    );
    await waitFor(() =>
      expect(recorded).toEqual([{ media_sha256: "c".repeat(64), answer_text: "" }]),
    );
  });

  it("saves an answer typed against a picture", async () => {
    // An image uploaded with an empty answer is useless until this
    // works. Kills on: an input with no save path — the operator types
    // the answer, leaves the screen, and it is gone.
    const saved: unknown[] = [];
    server.use(
      http.get(`/api/library/categories/${ID}`, () => HttpResponse.json(DETAIL)),
      http.put("/api/library/images/i1", async ({ request }) => {
        saved.push(await request.json());
        return HttpResponse.json(DETAIL.images[0]);
      }),
    );
    renderWithQuery(<CategoryEditor categoryId={ID} />);
    const answer = await screen.findByLabelText("Ответ i1");
    await userEvent.clear(answer);
    await userEvent.type(answer, "Аватар");
    await userEvent.tab();
    await waitFor(() =>
      expect(saved).toEqual([{ media_sha256: "a".repeat(64), answer_text: "Аватар" }]),
    );
  });

  it("renames the category through the edit route", async () => {
    const sent: unknown[] = [];
    server.use(
      http.get(`/api/library/categories/${ID}`, () => HttpResponse.json(DETAIL)),
      http.put(`/api/library/categories/${ID}`, async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json(DETAIL);
      }),
    );
    renderWithQuery(<CategoryEditor categoryId={ID} />);
    const title = await screen.findByLabelText("Название");
    await userEvent.clear(title);
    await userEvent.type(title, "Музыка");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(sent).toEqual([{ title: "Музыка", is_secret: false }]));
  });
});
