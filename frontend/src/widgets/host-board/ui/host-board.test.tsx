import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSelection } from "@/features/host-commands";
import { ATTACKER, DEFENDER, hostFrame, hostGroup } from "../../../../testing/host-frames";
import { HostBoard } from "./host-board";

const FRAME = hostFrame({
  current_player: ATTACKER,
  groups: [
    hostGroup({ id: "g1", owner: ATTACKER, cells: [{ col: 0, row: 0 }] }),
    hostGroup({
      id: "g2",
      owner: DEFENDER,
      cells: [{ col: 1, row: 0 }],
      category: { id: "c2", name: "Спорт" },
    }),
    hostGroup({
      id: "g3",
      owner: DEFENDER,
      cells: [{ col: 2, row: 2 }],
      category: { id: "c3", name: "Еда" },
    }),
  ],
  legal_attacks: { g1: ["g2"] },
});

describe("HostBoard", () => {
  beforeEach(() => {
    useSelection.getState().clear();
  });

  it("names every category, including another player's secret", () => {
    // §9.2: «Поле целиком со всеми категориями, включая чужие секреты» —
    // the exact opposite of the stage screen, and the reason the two
    // frames are different types.
    render(<HostBoard frame={FRAME} onDeclare={vi.fn()} />);
    expect(screen.getByText("Спорт")).toBeInTheDocument();
    expect(screen.getByText("Еда")).toBeInTheDocument();
    expect(screen.queryByText("Секрет")).toBeNull();
  });

  it("offers only groups that can attack as a first click", () => {
    // H2: `legal_attacks` has one key, so every other group is inert.
    render(<HostBoard frame={FRAME} onDeclare={vi.fn()} />);
    expect(screen.getByTestId("group-g1")).toBeEnabled();
    expect(screen.getByTestId("group-g2")).toBeDisabled();
    expect(screen.getByTestId("group-g3")).toBeDisabled();
  });

  it("dims illegal targets once an attacker is picked", async () => {
    // §9.2: «нелегальные цели гаснут, ярко только ортогонально смежные
    // чужие». Kills on: leaving every group bright and checking on click —
    // the operator would aim at a target that then refuses.
    render(<HostBoard frame={FRAME} onDeclare={vi.fn()} />);
    await userEvent.click(screen.getByTestId("group-g1"));
    expect(screen.getByTestId("group-g2")).toHaveAttribute("data-legal", "true");
    expect(screen.getByTestId("group-g3")).toHaveAttribute("data-legal", "false");
    expect(screen.getByTestId("group-g3")).toBeDisabled();
  });

  it("declares the attack on the second click", async () => {
    const onDeclare = vi.fn();
    render(<HostBoard frame={FRAME} onDeclare={onDeclare} />);
    await userEvent.click(screen.getByTestId("group-g1"));
    await userEvent.click(screen.getByTestId("group-g2"));
    expect(onDeclare).toHaveBeenCalledWith("g1", "g2");
  });

  it("cannot declare an illegal attack even when the click is forced", async () => {
    // H2 from the other side: this is the property, not the dimming.
    // Kills on: rendering an illegal target as a live button.
    const onDeclare = vi.fn();
    render(<HostBoard frame={FRAME} onDeclare={onDeclare} />);
    await userEvent.click(screen.getByTestId("group-g1"));
    await userEvent.click(screen.getByTestId("group-g3"));
    expect(onDeclare).not.toHaveBeenCalled();
  });

  it("lets the operator change their mind by re-picking the attacker", async () => {
    const onDeclare = vi.fn();
    render(<HostBoard frame={FRAME} onDeclare={onDeclare} />);
    await userEvent.click(screen.getByTestId("group-g1"));
    await userEvent.click(screen.getByTestId("group-g1"));
    expect(onDeclare).not.toHaveBeenCalled();
    expect(screen.getByTestId("group-g1")).toHaveAttribute("data-selected", "false");
  });

  it("offers nothing at all when no attack is legal", () => {
    // A stuck console is the intended failure (H2's cost note), and it
    // must not be a crash.
    render(<HostBoard frame={hostFrame({ legal_attacks: {} })} onDeclare={vi.fn()} />);
    expect(screen.getByTestId("group-g1")).toBeDisabled();
  });

  it("draws one outline per group, not one per cell", () => {
    // §9.1's rule applies to this board too: a group of N cells has to
    // read as one object.
    const frame = hostFrame({
      board: { width: 3, height: 1 },
      groups: [
        hostGroup({
          id: "g1",
          cells: [
            { col: 0, row: 0 },
            { col: 1, row: 0 },
          ],
        }),
      ],
      legal_attacks: {},
    });
    const { container } = render(<HostBoard frame={frame} onDeclare={vi.fn()} />);
    expect(container.querySelectorAll("[data-outline]")).toHaveLength(1);
  });
});
