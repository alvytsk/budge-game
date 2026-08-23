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

/** Invoke a button's React `onClick` directly, past the `disabled`
 * attribute that React itself honours before it ever reaches the handler.
 * It throws rather than no-ops if the handle is gone, so a React upgrade
 * that moved it fails here instead of making the test vacuous. */
function press(node: HTMLElement): void {
  const key = Object.keys(node).find((name) => name.startsWith("__reactProps$"));
  if (key === undefined) throw new Error(`no React props handle on ${node.dataset.testid}`);
  const props = (node as unknown as Record<string, { onClick?: () => void }>)[key];
  if (props?.onClick === undefined) throw new Error("button has no onClick");
  props.onClick();
}

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

  it("fills each group with its owner's colour", () => {
    // §9.1's colour rule binds the console as hard as it binds the
    // projector: the operator narrates ownership out loud and the two
    // screens have to agree. `colourOf` was widened to serve both boards,
    // and until now only the stage half was held. Kills on: a constant
    // fill, or looking the owner up in the wrong list.
    render(<HostBoard frame={FRAME} onDeclare={vi.fn()} />);
    expect(screen.getByTestId("group-g1")).toHaveStyle({ background: "#e4572e" });
    expect(screen.getByTestId("group-g2")).toHaveStyle({ background: "#2e86e4" });
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

  it("refuses an illegal target in the handler, not only in the markup", async () => {
    // The test above is stopped by `disabled` — React never calls the
    // handler for a disabled button, so it proves the dimming and nothing
    // about the code behind it. This one invokes the handler directly, so
    // H2 is held by the logic as well as by the attribute. Kills on:
    // dropping the `targets.includes` guard, which leaves the whole ruling
    // resting on one CSS-adjacent attribute.
    const onDeclare = vi.fn();
    render(<HostBoard frame={FRAME} onDeclare={onDeclare} />);
    await userEvent.click(screen.getByTestId("group-g1"));
    press(screen.getByTestId("group-g3"));
    expect(onDeclare).not.toHaveBeenCalled();
    // …and the same forcing does declare a legal one, so the assertion
    // above cannot pass merely because the forcing stopped working.
    press(screen.getByTestId("group-g2"));
    expect(onDeclare).toHaveBeenCalledWith("g1", "g2");
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

  it("still lays out a group the server sent with no cells", () => {
    // A cell-less group is a server bug, but this board is the operator's
    // live input surface: it has to stay laid out and keep every other
    // group clickable rather than emit `Infinity / span NaN` and collapse
    // the grid mid-show. Kills on: dropping `span`'s empty-cells guard,
    // where `Math.min(...[])` is `Infinity` and every arithmetic below it
    // turns to `NaN` — silently, because CSS just ignores the rule.
    const frame = hostFrame({
      groups: [
        hostGroup({ id: "g1", owner: ATTACKER, cells: [] }),
        hostGroup({ id: "g2", owner: DEFENDER, cells: [{ col: 1, row: 0 }] }),
      ],
      legal_attacks: { g2: ["g1"] },
    });
    render(<HostBoard frame={frame} onDeclare={vi.fn()} />);
    const empty = screen.getByTestId("group-g1");
    expect(empty.style.gridColumn).toBe("1 / span 1");
    expect(empty.style.gridRow).toBe("1 / span 1");
    expect(screen.getByTestId("group-g2")).toBeEnabled();
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
