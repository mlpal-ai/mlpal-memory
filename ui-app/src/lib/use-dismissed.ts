import { useState } from "react";

/** A persisted dismissable (first-run explainers): once closed, stays closed. */
export function useDismissed(key: string): [boolean, () => void] {
  const [dismissed, setDismissed] = useState(() => {
    try {
      return localStorage.getItem(key) === "1";
    } catch {
      return false;
    }
  });
  return [
    dismissed,
    () => {
      localStorage.setItem(key, "1");
      setDismissed(true);
    },
  ];
}
