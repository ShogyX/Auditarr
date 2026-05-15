/** Shared types echoing backend schemas. */

export interface HealthStatus {
  name: string;
  healthy: boolean;
  detail?: string;
  duration_ms?: number;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  version: string;
  checks: HealthStatus[];
}

export interface SystemInfo {
  name: string;
  version: string;
  env: string;
  python: string;
  platform: string;
  api_root: string;
  websocket_clients: number;
}

/**
 * Stage 5 (audit fix, Issue 11): payload of GET /system/version,
 * the lightweight probe purpose-built for the sidebar version
 * indicator. ``app_version`` is the image-stamped release version
 * — the one shown in release notes and the changelog — and is
 * what the sidebar should display. ``sdk_version`` is the
 * in-source schema version and only bumps on breaking releases.
 */
export interface SystemVersion {
  app_version: string;
  sdk_version: string;
}

export interface PluginSummary {
  id: string;
  name: string;
  version: string;
  type: string;
  capabilities: string[];
  routes: boolean;
}

export interface NavBadgeStats {
  issuesOpen?: number;
  rulesEnabled?: number;
  activeOptimizations?: number;
}
