import { useEffect, useState } from "react";

/** Debounce a fast-changing value (text-input filters) so server-side list
 * endpoints aren't hit on every keystroke. */
export function useDebounced<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}
