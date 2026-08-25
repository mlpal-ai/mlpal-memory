import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router";
import { Toaster } from "sonner";

import App from "@/App";
import { initTheme } from "@/lib/theme";
import "@/index.css";

initTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* HashRouter: the app is a StaticFiles mount at /ui — no server-side
        catch-all needed for deep links. */}
    <HashRouter>
      <App />
      <Toaster richColors position="top-right" />
    </HashRouter>
  </StrictMode>,
);
