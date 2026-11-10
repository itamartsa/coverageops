import { useEffect, useRef, useState } from "react";

/**
 * Width of a container in CSS px (ResizeObserver). Charts draw their SVG at this real width
 * (viewBox = px) so axis labels keep their font size instead of being scaled down in narrow
 * panels such as the 400 px device drawer or a 390 px phone.
 */
export function useWidth<T extends HTMLElement>(fallback = 640) {
  const ref = useRef<T>(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => { const cw = Math.round(el.getBoundingClientRect().width); if (cw > 0) setW(cw); };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}
