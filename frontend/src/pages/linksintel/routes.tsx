import { Route } from "react-router-dom";
import AnchorAnalysis from "./AnchorAnalysis";
import InternalLinks from "./InternalLinks";

// Authenticated routes for this module group (mounted inside the app shell).
export const routes = (
  <>
    <Route path="/links-intel" element={<AnchorAnalysis />} />
    <Route path="/links-intel/internal" element={<InternalLinks />} />
  </>
);
