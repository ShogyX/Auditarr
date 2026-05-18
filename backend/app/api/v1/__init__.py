"""API v1 routers."""

from fastapi import APIRouter

from app.api.v1 import (
    audit,
    auth,
    automation,
    dashboard,
    docs,
    extension_rules,
    health,
    integrations,
    libraries,
    media,
    notifications,
    optimization,
    path_mappings,
    playback,
    plugins,
    rule_templates,
    rules,
    runtime_settings,
    scans,
    system,
    tags,
    updater,
    webhooks,
)

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(system.router)
api_v1_router.include_router(plugins.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(audit.router)
api_v1_router.include_router(docs.router)
api_v1_router.include_router(libraries.router)
api_v1_router.include_router(media.router)
api_v1_router.include_router(scans.router)
api_v1_router.include_router(integrations.router)
api_v1_router.include_router(rules.router)
api_v1_router.include_router(rule_templates.router)
api_v1_router.include_router(automation.router)
api_v1_router.include_router(dashboard.router)
api_v1_router.include_router(notifications.router)
api_v1_router.include_router(optimization.router)
api_v1_router.include_router(updater.router)
api_v1_router.include_router(runtime_settings.router)
api_v1_router.include_router(path_mappings.router)
api_v1_router.include_router(extension_rules.router)
api_v1_router.include_router(playback.router)
api_v1_router.include_router(tags.router)
api_v1_router.include_router(webhooks.router)

__all__ = ["api_v1_router"]
