import {
  Activity,
  Archive,
  BookOpen,
  FileText,
  History,
  Home,
  MessageCircleQuestion,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plug,
  Search as SearchIcon,
  Sun,
  Waypoints,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router";

import { Brand } from "@/components/Brand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  type Identity,
  MemoryApiError,
  bootstrapIdentity,
  fetchDevIdentity,
  getStats,
  loadIdentity,
  saveIdentity,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { currentTheme, toggleTheme } from "@/lib/theme";
import { useWorkspace } from "@/lib/workspace";

const NAV = [
  { to: "/overview", label: "Overview", icon: Home },
  { to: "/ask", label: "Ask", icon: MessageCircleQuestion },
  { to: "/search", label: "Search", icon: SearchIcon },
  { to: "/documents", label: "Documents", icon: FileText },
  { to: "/episodes", label: "Episodes", icon: Activity },
  { to: "/timeline", label: "Timeline", icon: History },
  { to: "/manage", label: "Manage", icon: Archive },
  { to: "/graph", label: "Graph", icon: Waypoints },
  { to: "/connect", label: "Connect", icon: Plug },
];

const SIDEBAR_KEY = "mlpal-memory.sidebar";

export function Layout() {
  const [theme, setTheme] = useState(currentTheme());
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_KEY) === "collapsed",
  );
  const [identity, setIdentity] = useState<Identity>(loadIdentity);
  const [workspace, setWorkspace] = useWorkspace();
  const [workspaceOptions, setWorkspaceOptions] = useState<string[]>([]);
  // empty-tenant explainer: personal memory is owner-only, so a wrong identity
  // sees almost nothing — say so and offer the server's dev-identity hint.
  const [tenantDocs, setTenantDocs] = useState<number | null>(null);
  const [suggested, setSuggested] = useState<Identity | null>(null);
  // managed deployments reject the dev headers — without a key every call 401s,
  // which reads as "broken" unless we say what to do about it
  const [authNeeded, setAuthNeeded] = useState(false);

  // First run: no identity was ever chosen — adopt the server's hint instead of
  // a blind default (the founder hit this: user "user" owns nothing).
  useEffect(() => {
    void bootstrapIdentity().then((adopted) => {
      if (adopted) setIdentity(adopted);
    });
  }, []);

  // Datalist options mirror what the tenant's store actually holds.
  useEffect(() => {
    const load = () =>
      getStats()
        .then((s) => {
          setWorkspaceOptions(s.top_workspaces.map((w) => w.workspace));
          setTenantDocs(s.documents);
          setAuthNeeded(false);
        })
        .catch((err: unknown) => {
          setWorkspaceOptions([]);
          setAuthNeeded(
            err instanceof MemoryApiError && (err.status === 401 || err.status === 403),
          );
        });
    load();
    window.addEventListener("mlpal:identity-changed", load);
    return () => window.removeEventListener("mlpal:identity-changed", load);
  }, []);

  useEffect(() => {
    if (tenantDocs !== null && tenantDocs < 5) {
      void fetchDevIdentity().then((hint) => {
        if (hint && (hint.userId !== identity.userId || hint.orgId !== identity.orgId)) {
          setSuggested(hint);
        } else {
          setSuggested(null);
        }
      });
    } else {
      setSuggested(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantDocs, identity.orgId, identity.userId]);

  function toggleSidebar() {
    setCollapsed((v) => {
      localStorage.setItem(SIDEBAR_KEY, v ? "expanded" : "collapsed");
      return !v;
    });
  }

  function updateIdentity(patch: Partial<Identity>) {
    setIdentity((prev) => {
      const next = { ...prev, ...patch };
      saveIdentity(next);
      return next;
    });
  }

  return (
    // h-screen + overflow-hidden pins the shell to the viewport: long page
    // content scrolls inside <main>, never the body — so the sidebar's bottom
    // controls (identity, theme, collapse) are always reachable.
    <div className="flex h-screen overflow-hidden">
      <aside
        className={cn(
          "flex shrink-0 flex-col overflow-y-auto border-r border-border bg-card transition-[width] duration-200",
          collapsed ? "w-16" : "w-60",
        )}
      >
        <div className={cn("py-5", collapsed ? "px-0" : "px-5")}>
          {collapsed ? (
            <img
              src={`${import.meta.env.BASE_URL}logo.png`}
              alt="MLpal"
              className="mx-auto h-7 w-auto dark:invert"
            />
          ) : (
            <Brand subtitle="memory" />
          )}
        </div>
        <nav className={cn("flex flex-1 flex-col gap-1", collapsed ? "px-2" : "px-3")}>
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              title={collapsed ? label : undefined}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md py-2 text-sm font-medium transition-colors",
                  collapsed ? "justify-center px-0" : "px-3",
                  isActive
                    ? "bg-accent/15 font-semibold text-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )
              }
            >
              <Icon className="size-4 shrink-0" />
              {!collapsed && label}
            </NavLink>
          ))}
        </nav>
        <div className={cn("flex flex-col gap-1 p-3", collapsed && "items-center p-2")}>
          {!collapsed && (
            <div className="mb-2 flex flex-col gap-1.5 rounded-md border border-border p-2.5">
              <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Access
              </div>
              <Input
                type="password"
                value={identity.apiKey ?? ""}
                onChange={(e) => updateIdentity({ apiKey: e.target.value || undefined })}
                placeholder="API key (managed)"
                aria-label="API key"
                autoComplete="off"
                className="h-7 font-mono text-xs"
              />
              {!identity.apiKey && (
                <>
                  <Input
                    value={identity.orgId}
                    onChange={(e) => updateIdentity({ orgId: e.target.value })}
                    placeholder="org id (dev)"
                    aria-label="Org id"
                    className="h-7 font-mono text-xs"
                  />
                  <Input
                    value={identity.userId}
                    onChange={(e) => updateIdentity({ userId: e.target.value })}
                    placeholder="user id (dev)"
                    aria-label="User id"
                    className="h-7 font-mono text-xs"
                  />
                </>
              )}
            </div>
          )}
          {!collapsed && (
            <div className="mb-2 flex flex-col gap-1.5 rounded-md border border-border p-2.5">
              <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Workspace
              </div>
              <Input
                value={workspace}
                onChange={(e) => setWorkspace(e.target.value)}
                placeholder="all workspaces"
                aria-label="Workspace"
                list="mlpal-workspace-options"
                className="h-7 font-mono text-xs"
              />
              <datalist id="mlpal-workspace-options">
                {workspaceOptions.map((w) => (
                  <option key={w} value={w} />
                ))}
              </datalist>
              {workspace.trim() !== "" && (
                <span className="inline-flex w-fit max-w-full items-center gap-1 rounded-full bg-[var(--accent)] px-2 py-0.5 text-[10px] font-semibold text-[var(--accent-foreground)]">
                  <span className="truncate font-mono">{workspace.trim()}</span>
                  <button
                    onClick={() => setWorkspace("")}
                    aria-label="Clear workspace"
                    className="shrink-0 rounded-full transition-opacity hover:opacity-70"
                  >
                    <X className="size-3" />
                  </button>
                </span>
              )}
            </div>
          )}
          <a
            href="/docs"
            target="_blank"
            rel="noreferrer"
            title={collapsed ? "API reference" : undefined}
            className={cn(
              "flex items-center gap-3 rounded-md py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
              collapsed ? "justify-center px-2" : "px-3",
            )}
          >
            <BookOpen className="size-4" />
            {!collapsed && "API reference"}
          </a>
          <Button
            variant="ghost"
            size="sm"
            title={collapsed ? (theme === "dark" ? "Light mode" : "Dark mode") : undefined}
            className={cn("w-full", collapsed ? "justify-center" : "justify-start")}
            onClick={() => setTheme(toggleTheme())}
          >
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            {!collapsed && (theme === "dark" ? "Light mode" : "Dark mode")}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className={cn("w-full", collapsed ? "justify-center" : "justify-start")}
            onClick={toggleSidebar}
          >
            {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
            {!collapsed && "Collapse"}
          </Button>
        </div>
      </aside>
      <main className="atmos flex-1 overflow-auto">
        <div className="page-anim mx-auto max-w-5xl px-8 py-8">
          {authNeeded && (
            <div className="mb-6 flex flex-wrap items-center gap-3 rounded-lg border border-[var(--warning)]/40 bg-[var(--warning-bg)] px-4 py-3 text-sm">
              <span>
                This deployment requires an API key. Paste a key with the{" "}
                <code className="font-mono">memory.read</code> scope into the{" "}
                <strong>Access</strong> box in the sidebar. The Connect page explains how
                to create one.
              </span>
              <NavLink
                to="/connect"
                className="rounded-md border border-[var(--warning)]/50 px-2.5 py-1 text-xs font-medium transition-colors hover:bg-[var(--warning)]/10"
              >
                Open Connect
              </NavLink>
            </div>
          )}
          {suggested && (
            <div className="mb-6 flex flex-wrap items-center gap-3 rounded-lg border border-[var(--warning)]/40 bg-[var(--warning-bg)] px-4 py-3 text-sm">
              <span>
                This identity (<code className="font-mono">{identity.orgId}</code> /{" "}
                <code className="font-mono">{identity.userId}</code>) sees only{" "}
                {tenantDocs} document{tenantDocs === 1 ? "" : "s"} — personal memory is
                owner-only. The local corpus is owned by{" "}
                <code className="font-mono">{suggested.userId}</code>.
              </span>
              <button
                onClick={() => {
                  saveIdentity(suggested);
                  setIdentity(suggested);
                  setSuggested(null);
                }}
                className="rounded-md border border-[var(--warning)]/50 px-2.5 py-1 text-xs font-medium transition-colors hover:bg-[var(--warning)]/10"
              >
                Switch to {suggested.orgId} / {suggested.userId}
              </button>
            </div>
          )}
          <Outlet />
        </div>
      </main>
    </div>
  );
}
