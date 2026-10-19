import { Route } from "react-router-dom";
import { AgentTeams, ContentPlan, Images, Prompts, Providers, TopicalMap, Usage } from "./Platform";
import Skills from "./Skills";
import { BlogGenerator, LandingGenerator, NewsGenerator, PlaybookGenerator, PublicationGenerator, SocialPostsGenerator, WebsiteGenerator, WhitepaperGenerator } from "./Studio";

// Authenticated routes for this module group (mounted inside the app shell).
export const routes = (
  <>
    <Route path="/studio/blog" element={<BlogGenerator />} />
    <Route path="/studio/news" element={<NewsGenerator />} />
    <Route path="/studio/whitepaper" element={<WhitepaperGenerator />} />
    <Route path="/studio/publication" element={<PublicationGenerator />} />
    <Route path="/studio/playbook" element={<PlaybookGenerator />} />
    <Route path="/studio/website" element={<WebsiteGenerator />} />
    <Route path="/studio/landing-pages" element={<LandingGenerator />} />
    <Route path="/studio/social-posts" element={<SocialPostsGenerator />} />
    <Route path="/studio/images" element={<Images />} />
    <Route path="/ai/providers" element={<Providers />} />
    <Route path="/ai/prompts" element={<Prompts />} />
    <Route path="/ai/skills" element={<Skills />} />
    <Route path="/ai/agent-teams" element={<AgentTeams />} />
    <Route path="/ai/usage" element={<Usage />} />
    <Route path="/topical-map" element={<TopicalMap />} />
    <Route path="/content-plan" element={<ContentPlan />} />
  </>
);
