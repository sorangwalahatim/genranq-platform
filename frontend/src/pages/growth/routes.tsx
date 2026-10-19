import { Route } from "react-router-dom";
import Attribution from "./Attribution";
import Connectors from "./Connectors";
import { Calendly, Hubspot } from "./Crm";
import Forms from "./Forms";
import Leads from "./Leads";
import Marketplace from "./Marketplace";
import Onboarding from "./Onboarding";
import Orders from "./Orders";
import { FreeTool, FreeToolsIndex, PublicForm } from "./Public";
import Services from "./Services";
import { ToolPage, ToolsHome } from "./Tools";
import Wallet from "./Wallet";

// Authenticated routes for this module group (mounted inside the app shell).
export const routes = (
  <>
    <Route path="/leads" element={<Leads />} />
    <Route path="/leads/hubspot" element={<Hubspot />} />
    <Route path="/leads/calendly" element={<Calendly />} />
    <Route path="/forms" element={<Forms />} />
    <Route path="/forms/connectors" element={<Connectors />} />
    <Route path="/attribution" element={<Attribution />} />
    <Route path="/onboarding" element={<Onboarding />} />
    <Route path="/services" element={<Services />} />
    <Route path="/marketplace" element={<Marketplace />} />
    <Route path="/marketplace/orders" element={<Orders />} />
    <Route path="/wallet" element={<Wallet />} />
    <Route path="/tools" element={<ToolsHome />} />
    <Route path="/tools/:tool" element={<ToolPage />} />
  </>
);

// Public (unauthenticated) routes: embeddable forms, free tools, public backlink monitor.
export const publicRoutes = (
  <>
    <Route path="/f/:token" element={<PublicForm />} />
    <Route path="/free" element={<FreeToolsIndex />} />
    <Route path="/free/:tool" element={<FreeTool />} />
  </>
);
