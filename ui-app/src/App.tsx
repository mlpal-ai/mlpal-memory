import { Navigate, Route, Routes } from "react-router";

import { Layout } from "@/components/Layout";
import { Ask } from "@/pages/Ask";
import { Connect } from "@/pages/Connect";
import { Documents } from "@/pages/Documents";
import { Episodes } from "@/pages/Episodes";
import { Graph } from "@/pages/Graph";
import { Manage } from "@/pages/Manage";
import { Overview } from "@/pages/Overview";
import { Search } from "@/pages/Search";
import { Timeline } from "@/pages/Timeline";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/overview" replace />} />
        <Route path="/overview" element={<Overview />} />
        <Route path="/ask" element={<Ask />} />
        <Route path="/search" element={<Search />} />
        <Route path="/documents" element={<Documents />} />
        <Route path="/episodes" element={<Episodes />} />
        <Route path="/timeline" element={<Timeline />} />
        <Route path="/manage" element={<Manage />} />
        <Route path="/graph" element={<Graph />} />
        <Route path="/connect" element={<Connect />} />
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Route>
    </Routes>
  );
}
