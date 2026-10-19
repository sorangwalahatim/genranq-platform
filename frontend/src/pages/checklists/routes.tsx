import { Route } from "react-router-dom";
import ChecklistsPage from "./ChecklistsPage";
import RedirectsPage from "./RedirectsPage";

// Authenticated routes for this module group (mounted inside the app shell).
export const routes = (
  <>
    <Route path="/checklists" element={<ChecklistsPage />} />
    <Route path="/redirects" element={<RedirectsPage />} />
  </>
);
