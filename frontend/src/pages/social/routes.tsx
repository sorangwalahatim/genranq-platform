import { Route } from "react-router-dom";
import Calendar from "./Calendar";
import ContentAutomation from "./ContentAutomation";
import Extension from "./Extension";
import ReportAutomation from "./ReportAutomation";
import SocialAccounts from "./SocialAccounts";
import SocialAnalytics from "./SocialAnalytics";
import SocialAutomation from "./SocialAutomation";
import TeamsNotifications from "./TeamsNotifications";
import WhiteLabel from "./WhiteLabel";

// Authenticated routes for this module group (mounted inside the app shell).
export const routes = (
  <>
    <Route path="/social/accounts" element={<SocialAccounts />} />
    <Route path="/calendar" element={<Calendar />} />
    <Route path="/social/automation" element={<SocialAutomation />} />
    <Route path="/automation/content" element={<ContentAutomation />} />
    <Route path="/social/analytics" element={<SocialAnalytics />} />
    <Route path="/reports/automation" element={<ReportAutomation />} />
    <Route path="/notifications/teams" element={<TeamsNotifications />} />
    <Route path="/settings/white-label" element={<WhiteLabel />} />
    <Route path="/extension" element={<Extension />} />
  </>
);
