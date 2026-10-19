import { Route } from "react-router-dom";
import Agents from "./Agents";
import { ExecutiveDashboard, TeamDashboard } from "./Dashboards";
import IssuesInbox from "./IssuesInbox";
import PageDashboard from "./PageDashboard";
import ProjectMonitor from "./ProjectMonitor";
import Workload from "./Workload";
import Workstreams from "./Workstreams";

// Authenticated routes for this module group (mounted inside the app shell).
export const routes = (
  <>
    <Route path="/projects/:id/monitor" element={<ProjectMonitor />} />
    <Route path="/projects/:id/monitor/page" element={<PageDashboard />} />
    <Route path="/projects/:id/workstreams" element={<Workstreams />} />
    <Route path="/issues" element={<IssuesInbox />} />
    <Route path="/dashboards/team/:team" element={<TeamDashboard />} />
    <Route path="/dashboards/executive" element={<ExecutiveDashboard />} />
    <Route path="/workload" element={<Workload />} />
    <Route path="/agents" element={<Agents />} />
  </>
);
