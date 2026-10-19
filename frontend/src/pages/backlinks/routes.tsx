import { Route } from "react-router-dom";
import Backlinks from "./Backlinks";
import KeywordResearch from "./KeywordResearch";
import PageCompare from "./PageCompare";
import Provider from "./Provider";
import Relevink from "./Relevink";
import Suppliers from "./Suppliers";

// Authenticated routes for this module group (mounted inside the app shell).
export const routes = (
  <>
    <Route path="/backlinks" element={<Backlinks />} />
    <Route path="/backlinks/suppliers" element={<Suppliers />} />
    <Route path="/backlinks/ahrefs" element={<Provider key="ahrefs" provider="ahrefs" />} />
    <Route path="/backlinks/semrush" element={<Provider key="semrush" provider="semrush" />} />
    <Route path="/relevink" element={<Relevink />} />
    <Route path="/keyword-research" element={<KeywordResearch />} />
    <Route path="/competitors/pages" element={<PageCompare />} />
  </>
);
