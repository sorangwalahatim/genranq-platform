import { Route } from "react-router-dom";
import LoadingTime from "./LoadingTime";
import RunDetail from "./RunDetail";
import SkillsGrid from "./SkillsGrid";

// Authenticated routes for this module group (mounted inside the app shell).
export const routes = (
  <>
    <Route path="/audit-skills" element={<SkillsGrid />} />
    <Route path="/audit-skills/runs/:id" element={<RunDetail />} />
    <Route path="/loading-time" element={<LoadingTime />} />
  </>
);
