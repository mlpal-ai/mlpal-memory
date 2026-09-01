import { useEffect, useState } from "react";

/**
 * The global workspace — one selector in the sidebar, read everywhere.
 *
 * Persisted and broadcast exactly like the dev identity, so every page's
 * workspace input is a view over the same value: editing it anywhere (the
 * sidebar or a page's own input) updates all of them. Empty = all workspaces.
 */

const WORKSPACE_KEY = "mlpal.memory.workspace";
const WORKSPACE_EVENT = "mlpal:workspace-changed";

export function loadWorkspace(): string {
  try {
    return localStorage.getItem(WORKSPACE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function saveWorkspace(workspace: string): void {
  localStorage.setItem(WORKSPACE_KEY, workspace);
  window.dispatchEvent(new CustomEvent(WORKSPACE_EVENT));
}

/** The global workspace as state: initializes from storage, stays in sync
 * with edits made on any other mounted input, writes back on set. */
export function useWorkspace(): [string, (workspace: string) => void] {
  const [workspace, setWorkspace] = useState(loadWorkspace);
  useEffect(() => {
    const sync = () => setWorkspace(loadWorkspace());
    window.addEventListener(WORKSPACE_EVENT, sync);
    return () => window.removeEventListener(WORKSPACE_EVENT, sync);
  }, []);
  return [workspace, saveWorkspace];
}
