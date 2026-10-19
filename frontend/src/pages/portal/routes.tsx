import { Route } from "react-router-dom";
import ClientDashboard from "./ClientDashboard";
import MessagesHub from "./MessagesHub";
import InternalSpace from "./InternalSpace";

// Client Portal routes (mounted inside the app shell).
export const routes = (
  <>
    <Route path="/portal/:projectId" element={<ClientDashboard />} />
    <Route path="/portal/:projectId/messages" element={<MessagesHub />} />
    <Route path="/portal/:projectId/team" element={<InternalSpace />} />
  </>
);
