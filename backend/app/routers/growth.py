"""Growth group router (Phase 6 — Leads, Marketplace & Integrations).

Endpoints live in app/services/growth_api_*.py and are aggregated here:
  leads/forms/connectors/attribution/HubSpot/Calendly/onboarding → growth_api_leads
  services/marketplace/orders/wallet/platform-operator queues   → growth_api_market
  free tools workspace + monitors                               → growth_api_tools
  unauthenticated /public/* (forms, webhooks, free tools)       → growth_api_public
"""
from fastapi import APIRouter

from ..services import growth_api_leads, growth_api_market, growth_api_public, growth_api_tools

router = APIRouter(tags=["growth"])
router.include_router(growth_api_public.router)
router.include_router(growth_api_leads.router)
router.include_router(growth_api_market.router)
router.include_router(growth_api_tools.router)
