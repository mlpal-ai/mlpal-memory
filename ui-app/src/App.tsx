import { Navigate, Route, Routes } from "react-router";

import { Layout } from "@/components/Layout";
import { Ask } from "@/pages/Ask";
import { Documents } from "@/pages/Documents";
import { Episodes } from "@/pages/Episodes";
import { Graph } from "@/pages/Graph";
import { Overview } from "@/pages/Overview";
import { Search } from "@/pages/Search";

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
        <Route path="/graph" element={<Graph />} />
        <Route path="*" element={<Navigate to="/overview" replace />} />
      </Route>
    </Routes>
  );
}
