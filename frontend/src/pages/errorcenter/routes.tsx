import { Route } from "react-router-dom";
import ErrorInbox from "./ErrorInbox";
import IgnoredView from "./IgnoredView";

// Authenticated routes for this module group (mounted inside the app shell).
// NOTE (wiring): in frontend/src/main.tsx add:
//   import { routes as errorCenterRoutes } from "./pages/errorcenter/routes";
// and render {errorCenterRoutes} inside the <Routes> next to the other module routes.
export const routes = (
  <>
    <Route path="/error-center" element={<ErrorInbox />} />
    <Route path="/error-center/ignored" element={<IgnoredView />} />
  </>
);
