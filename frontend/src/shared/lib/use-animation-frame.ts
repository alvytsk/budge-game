import { useEffect, useState } from "react";

/** A counter that increases once per painted frame while `active`.
 *
 * §7.3 asks for `requestAnimationFrame` interpolation. Components read
 * the returned value only to re-render; the *time* always comes from
 * `useServerClock`, never from this counter.
 */
export function useAnimationFrame(active: boolean): number {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!active) return;
    let handle = 0;
    const step = () => {
      setTick((previous) => previous + 1);
      handle = requestAnimationFrame(step);
    };
    handle = requestAnimationFrame(step);
    return () => cancelAnimationFrame(handle);
  }, [active]);

  return tick;
}
