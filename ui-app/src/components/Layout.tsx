import {
  Activity,
  BookOpen,
  FileText,
  Home,
  MessageCircleQuestion,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Search as SearchIcon,
  Sun,
  Waypoints,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router";

import { Brand } from "@/components/Brand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { type Identity, loadIdentity, saveIdentity } from "@/lib/api";
import { cn } from "@/lib/cn";
import { currentTheme, toggleTheme } from "@/lib/theme";

const NAV = [
  { to: "/overview", label: "Overview", icon: Home },
  { to: "/ask", label: "Ask", icon: MessageCircleQuestion },
  { to: "/search", label: "Search", icon: SearchIcon },
  { to: "/documents", label: "Documents", icon: FileText },
  { to: "/episodes", label: "Episodes", icon: Activity },
  { to: "/graph", label: "Graph", icon: Waypoints },
];

const SIDEBAR_KEY = "mlpal-memory.sidebar";

export function Layout() {
  const [theme, setTheme] = useState(currentTheme());
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(SIDEBAR_KEY) === "collapsed",
  );
  const [identity, setIdentity] = useState<Identity>(loadIdentity);

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
                Dev identity
              </div>
              <Input
                value={identity.orgId}
                onChange={(e) => updateIdentity({ orgId: e.target.value })}
                placeholder="org id"
                aria-label="Org id"
                className="h-7 font-mono text-xs"
              />
              <Input
                value={identity.userId}
                onChange={(e) => updateIdentity({ userId: e.target.value })}
                placeholder="user id"
                aria-label="User id"
                className="h-7 font-mono text-xs"
              />
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
          <Outlet />
        </div>
      </main>
    </div>
  );
}
