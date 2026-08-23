import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import type { ReadinessBody } from "@/shared/api";
import { renderWithQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { LibraryPage } from "./library-page";

const CATEGORIES = [
  { id: "a", title: "Кино", is_secret: false, is_active: true, version: 1, active_image_count: 12 },
  { id: "b", title: "Тайна", is_secret: true, is_active: true, version: 1, active_image_count: 3 },
];

const READY = {
  cells: 12,
  threshold: 5,
  ordinary_available: 40,
  secrets_available: 6,
  thin: [],
  ready: true,
};

function stock(readiness: ReadinessBody = READY) {
  server.use(
    http.get("/api/library/categories", () => HttpResponse.json(CATEGORIES)),
    http.get("/api/library/readiness", () => HttpResponse.json(readiness)),
    http.get("/api/library/categories/:id", () =>
      HttpResponse.json({ category: CATEGORIES[0], images: [] }),
    ),
  );
}

describe("LibraryPage", () => {
  it("lists every category with its picture count", async () => {
    stock();
    renderWithQuery(<LibraryPage />);
    expect(await screen.findByText("Кино")).toBeInTheDocument();
    expect(screen.getByTestId("count-a")).toHaveTextContent("12");
  });

  it("marks which categories are secrets", async () => {
    // §2.3: a secret is a different kind of thing, and the operator picks
    // one per player. Kills on: rendering both alike.
    stock();
    renderWithQuery(<LibraryPage />);
    await screen.findByText("Тайна");
    expect(screen.getByTestId("category-b")).toHaveAttribute("data-secret", "true");
    expect(screen.getByTestId("category-a")).toHaveAttribute("data-secret", "false");
  });

  it("warns softly when the library is thin, and refuses nothing", async () => {
    // §8: «мягкое предупреждение». Kills on: turning this into a block —
    // an operator who wants the show to go on must be able to start it.
    stock({ ...READY, ready: false, thin: [{ id: "b", title: "Тайна", active_image_count: 3 }] });
    renderWithQuery(<LibraryPage />);
    await screen.findByText("Кино");
    expect(screen.getByTestId("readiness")).toHaveAttribute("data-ready", "false");
    expect(screen.getByTestId("readiness")).toHaveTextContent("Тайна");
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("opens a category for editing when it is picked", async () => {
    stock();
    renderWithQuery(<LibraryPage />);
    await userEvent.click(await screen.findByText("Кино"));
    await waitFor(() => expect(screen.getByLabelText("Название")).toBeInTheDocument());
  });

  it("creates a category from the form", async () => {
    const made: unknown[] = [];
    stock();
    server.use(
      http.post("/api/library/categories", async ({ request }) => {
        made.push(await request.json());
        return HttpResponse.json(CATEGORIES[0], { status: 201 });
      }),
    );
    renderWithQuery(<LibraryPage />);
    await userEvent.type(await screen.findByLabelText("Новая тема"), "Музыка");
    await userEvent.click(screen.getByRole("button", { name: "Создать" }));
    await waitFor(() => expect(made).toEqual([{ title: "Музыка", is_secret: false }]));
  });
});
