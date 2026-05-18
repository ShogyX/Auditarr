import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/shell/AppShell";
import { RequireAuth } from "@/components/shell/RequireAuth";
import { AccountPage } from "@/features/account/AccountPage";
import { AuditLogPage } from "@/features/audit/AuditLogPage";
import { DashboardPage } from "@/features/dashboard/DashboardPage";
import { ForcedChangePasswordPage } from "@/features/auth/ForcedChangePasswordPage";
import { ForgotPasswordPage } from "@/features/auth/ForgotPasswordPage";
import { LoginPage } from "@/features/auth/LoginPage";
import { ResetPasswordPage } from "@/features/auth/ResetPasswordPage";
import { FilesPage } from "@/features/files/FilesPage";
import { ChangelogPage } from "@/features/help/ChangelogPage";
import { HelpPage } from "@/features/help/HelpPage";
import { IntegrationsPage } from "@/features/integrations/IntegrationsPage";
import { NotificationsPage } from "@/features/notifications/NotificationsPage";
import { OptimizationPage } from "@/features/optimization/OptimizationPage";
import { PluginsPage } from "@/features/plugins/PluginsPage";
import { RulesPage } from "@/features/rules/RulesPage";
import { RuleEditorPage } from "@/features/rules/RuleEditorPage";
import { ScanDetailPage } from "@/features/scans/ScanDetailPage";
import { SettingsPage } from "@/features/settings/SettingsPage";
import { LogsPage } from "@/features/system/LogsPage";
import { usePluginPages } from "@/plugins/registry";

export function AppRoutes() {
  const pluginPages = usePluginPages();

  return (
    <Routes>
      <Route path="login" element={<LoginPage />} />
      <Route path="forgot" element={<ForgotPasswordPage />} />
      <Route path="reset-password" element={<ResetPasswordPage />} />
      {/* Stage 12 (plan §584) — forced change-password screen
          for users with must_change_password=true. Lives
          outside the RequireAuth shell because the user has
          a session but isn't allowed into the app yet. */}
      <Route path="change-password" element={<ForcedChangePasswordPage />} />

      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="files" element={<FilesPage />} />
        <Route path="rules" element={<RulesPage />} />
        {/* Stage 30: routed full-screen rule editor. The list
            page navigates here for both create and edit instead
            of opening a modal. ``new`` is its own route so the
            "create" surface is bookmarkable; edit takes a
            ``ruleId`` param so a half-finished edit URL is
            shareable too. */}
        <Route path="rules/new" element={<RuleEditorPage />} />
        <Route path="rules/:ruleId/edit" element={<RuleEditorPage />} />
        {/* Stage 10 audit fix (Issue 15): Automation is now a tab
            on the Rules page. Existing bookmarks to /automation
            land at /rules?tab=automation so links don't 404. The
            standalone AutomationPage component is preserved (and
            still tested in test-pages.test.tsx) but no route mounts
            it now. */}
        <Route
          path="automation"
          element={<Navigate to="/rules?tab=automation" replace />}
        />
        <Route path="optimization" element={<OptimizationPage />} />
        {/* Stage 14 (audit follow-up): per-scan detail. Triggered
            from the Dashboard's Recent scans card. */}
        <Route path="scans/:scanId" element={<ScanDetailPage />} />
        <Route path="integrations" element={<IntegrationsPage />} />
        <Route path="notifications" element={<NotificationsPage />} />
        <Route path="plugins" element={<PluginsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        {/* Stage 14 (audit follow-up): audit log viewer. Admin-only
            on the backend; the page itself doesn't gate access — a
            non-admin user just sees the API's 403 error state. */}
        <Route path="settings/audit" element={<AuditLogPage />} />
        {/* v1.9 Stage 8.1 — Logs page. Admin-only at the API
            layer; non-admins see the React Query 403 error
            surface inside the page itself. */}
        <Route path="system/logs" element={<LogsPage />} />
        <Route path="help" element={<HelpPage />} />
        {/* Stage 5 (audit follow-up): self-service account page.
            Linked from the avatar button in TopNav. */}
        <Route path="account" element={<AccountPage />} />
        {/* Stage 12 audit fix (Issue 17): Changelog moved out of
            Help into its own route. Hosts the updater panel +
            CHANGELOG.md content. */}
        <Route path="changelog" element={<ChangelogPage />} />

        {pluginPages.map((page) => {
          const C = page.component;
          return (
            <Route
              key={page.key}
              path={`plugins/${page.key}/${page.path ?? ""}`.replace(/\/+$/, "")}
              element={<C />}
            />
          );
        })}

        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
