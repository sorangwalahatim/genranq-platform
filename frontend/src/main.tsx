import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, HashRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { DEMO, installDemo } from "./demo";
import "./styles.css";
import Layout from "./components/Layout";
import { Loading } from "./components/ui";
import { getSession } from "./lib/api";
import { MeProvider, ToastHost, useMe } from "./lib/hooks";
import { AcceptInvite, Login, Signup } from "./pages/Auth";
import Dashboard from "./pages/Dashboard";
import { Projects, ProjectDetail } from "./pages/Projects";
import Tasks from "./pages/Tasks";
import Time from "./pages/Time";
import Team from "./pages/Team";
import Approvals from "./pages/Approvals";
import Activity from "./pages/Activity";
import RankTracker from "./pages/RankTracker";
import { AuditDetail, Audits } from "./pages/Audits";
import { ActionPlan, ActionPlans } from "./pages/ActionPlans";
import Reports from "./pages/Reports";
import Automation from "./pages/Automation";
import Integrations from "./pages/Integrations";
import Vault from "./pages/Vault";
import Billing from "./pages/Billing";
import Jobs from "./pages/Jobs";
import Spec from "./pages/Spec";
import ModulePage from "./pages/ModulePage";
import Settings from "./pages/Settings";
import { Checklist, Competitors, PageSpeed } from "./pages/SeoMisc";
import { routes as backlinkRoutes } from "./pages/backlinks/routes";
import { routes as monitorRoutes } from "./pages/monitor/routes";
import { routes as localRoutes } from "./pages/local/routes";
import { routes as aiRoutes } from "./pages/ai/routes";
import { routes as socialRoutes } from "./pages/social/routes";
import { publicRoutes as growthPublicRoutes, routes as growthRoutes } from "./pages/growth/routes";
import { routes as checklistRoutes } from "./pages/checklists/routes";
import { routes as auditSkillRoutes } from "./pages/auditskills/routes";
import { routes as errorCenterRoutes } from "./pages/errorcenter/routes";
import { routes as portalRoutes } from "./pages/portal/routes";
import { routes as linksIntelRoutes } from "./pages/linksintel/routes";

try { const t = localStorage.getItem("genranq.theme"); if (t) document.documentElement.dataset.theme = t; } catch { /* ignore */ }

function Guard() {
  const { me } = useMe();
  const loc = useLocation();
  if (!getSession()) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  if (!me) return <Loading />;
  return <Layout />;
}

if (DEMO) installDemo();
const Router = DEMO ? HashRouter : BrowserRouter;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {DEMO && <div className="demo-strip" role="note">Read-only snapshot of GENRANQ{(window as any).__GQ_DEMO_AT__ ? ` · data as of ${(window as any).__GQ_DEMO_AT__}` : ""} · actions are disabled</div>}
    <Router>
      <ToastHost>
        <MeProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/signup" element={<Signup />} />
            <Route path="/accept-invite" element={<AcceptInvite />} />
            {growthPublicRoutes}
            <Route element={<Guard />}>
              <Route path="/" element={<Dashboard />} />
              <Route path="/projects" element={<Projects />} />
              <Route path="/projects/:id" element={<ProjectDetail />} />
              <Route path="/tasks" element={<Tasks />} />
              <Route path="/time" element={<Time />} />
              <Route path="/team" element={<Team />} />
              <Route path="/approvals" element={<Approvals />} />
              <Route path="/activity" element={<Activity />} />
              <Route path="/rank-tracker" element={<RankTracker />} />
              <Route path="/keywords" element={<RankTracker />} />
              <Route path="/audits" element={<Audits />} />
              <Route path="/crawls" element={<Audits />} />
              <Route path="/audits/:id" element={<AuditDetail />} />
              <Route path="/action-plans" element={<ActionPlans />} />
              <Route path="/action-plans/:id" element={<ActionPlan />} />
              <Route path="/reports" element={<Reports />} />
              <Route path="/automation" element={<Automation />} />
              <Route path="/integrations" element={<Integrations />} />
              <Route path="/integrations/:key" element={<Integrations />} />
              <Route path="/vault" element={<Vault />} />
              <Route path="/billing" element={<Billing />} />
              <Route path="/jobs" element={<Jobs />} />
              <Route path="/spec" element={<Spec />} />
              <Route path="/checklist" element={<Checklist />} />
              <Route path="/pagespeed" element={<PageSpeed />} />
              <Route path="/competitors" element={<Competitors />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/settings/branding" element={<Settings />} />
              <Route path="/m/:num" element={<ModulePage />} />
              {monitorRoutes}
              {backlinkRoutes}
              {localRoutes}
              {aiRoutes}
              {socialRoutes}
              {growthRoutes}
              {checklistRoutes}
              {auditSkillRoutes}
              {errorCenterRoutes}
              {portalRoutes}
              {linksIntelRoutes}
              <Route path="*" element={<div className="empty"><h3>Page not found</h3></div>} />
            </Route>
          </Routes>
        </MeProvider>
      </ToastHost>
    </Router>
  </React.StrictMode>
);
