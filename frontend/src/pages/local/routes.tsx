import { Route } from "react-router-dom";
import Automation from "./Automation";
import Categories from "./Categories";
import Competitors from "./Competitors";
import GbpAudit from "./GbpAudit";
import GmbPosts from "./GmbPosts";
import Grid from "./Grid";
import LocalScan from "./LocalScan";
import PostAudit from "./PostAudit";
import Reviews from "./Reviews";
import WebsiteGbp from "./WebsiteGbp";

// Authenticated routes for this module group (mounted inside the app shell).
export const routes = (
  <>
    <Route path="/local/gbp-audit" element={<GbpAudit />} />
    <Route path="/local/categories" element={<Categories />} />
    <Route path="/local/reviews" element={<Reviews />} />
    <Route path="/local/posts" element={<PostAudit />} />
    <Route path="/local/scan" element={<LocalScan />} />
    <Route path="/local/grid" element={<Grid />} />
    <Route path="/local/competitors" element={<Competitors />} />
    <Route path="/local/website-gbp" element={<WebsiteGbp />} />
    <Route path="/local/gmb-posts" element={<GmbPosts />} />
    <Route path="/local/automation" element={<Automation />} />
  </>
);
