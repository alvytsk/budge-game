import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { renderWithQuery } from "../../../../testing/query";
import { server } from "../../../../testing/server";
import { LoginPage } from "./login-page";

describe("LoginPage", () => {
  it("submits the password the operator typed", async () => {
    const seen: string[] = [];
    server.use(
      http.post("/api/session", async ({ request }) => {
        seen.push(((await request.json()) as { password: string }).password);
        return new HttpResponse(null, { status: 204 });
      }),
      http.get("/api/matches", () => HttpResponse.json([])),
    );
    renderWithQuery(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Пароль"), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    await waitFor(() => expect(seen).toEqual(["hunter2"]));
  });

  it("says the password was wrong, and keeps the field usable", async () => {
    // Kills on: clearing the form or unmounting it on a 401 — the
    // operator retypes on a screen that just lost their input.
    server.use(http.post("/api/session", () => new HttpResponse(null, { status: 401 })));
    renderWithQuery(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Пароль"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByText("Неверный пароль")).toBeInTheDocument();
    expect(screen.getByLabelText("Пароль")).toBeEnabled();
  });

  it("does not put the password on screen as readable text", () => {
    // The console is often operated with a room watching the screen.
    renderWithQuery(<LoginPage />);
    expect(screen.getByLabelText("Пароль")).toHaveAttribute("type", "password");
  });
});
