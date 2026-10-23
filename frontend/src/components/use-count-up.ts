"use client";

/** Animated count-up hook for stat figures (numbers only). */

import { useEffect, useState } from "react";

export function useCountUp(target: number, active = true, duration = 700): number {
  const [value, setValue] = useState(active ? 0 : target);
  useEffect(() => {
    if (!active) {
      setValue(target);
      return;
    }
    let raf = 0;
    const t0 = performance.now();
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      setValue(Math.round(target * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, active, duration]);
  return value;
}
