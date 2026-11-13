# v3 API Inventory

Generated from `openapi-v3.json` (FastAPI, v3.4.0). **144 operations** across 110 paths, grouped by first path segment.

Use this as the checklist for the v4 API coverage test. The 4 Streamlit stub routes are not features and are not ported.

| Group | Operations |
|-------|-----------:|
| `(streamlit stubs)` | 4 |
| `admin` | 2 |
| `audit-log` | 1 |
| `auth` | 9 |
| `configuration` | 6 |
| `dashboard` | 1 |
| `document-types` | 5 |
| `dut-categories` | 1 |
| `dut-types` | 5 |
| `estimations` | 24 |
| `feature-categories` | 1 |
| `feature-presets` | 4 |
| `features` | 7 |
| `healthcheck` | 1 |
| `historical-projects` | 4 |
| `host-config` | 1 |
| `import` | 2 |
| `integrations` | 12 |
| `notifications` | 5 |
| `profiles` | 4 |
| `project-types` | 1 |
| `public-holidays` | 5 |
| `reports` | 2 |
| `requests` | 8 |
| `risk-items` | 4 |
| `task-presets` | 4 |
| `task-templates` | 4 |
| `team-members` | 4 |
| `teams` | 5 |
| `users` | 6 |
| `webhooks` | 1 |
| `working-weeks` | 1 |

## `(streamlit stubs)`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/_stcore/health` | Stcore Stub |
| GET | `/_stcore/host-config` | Stcore Stub |
| GET | `/{path}/_stcore/health` | Stcore Stub |
| GET | `/{path}/_stcore/host-config` | Stcore Stub |

## `admin`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/admin/backup` | Admin Backup |
| POST | `/api/admin/restore` | Admin Restore |

## `audit-log`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/audit-log` | List Audit Log |

## `auth`

| Method | Path | Summary |
|--------|------|---------|
| POST | `/api/auth/change-password` | Change Password |
| POST | `/api/auth/ldap/reset-synced-users` | Ldap Reset Synced Users |
| POST | `/api/auth/ldap/sync` | Ldap Sync |
| POST | `/api/auth/ldap/test` | Ldap Test |
| POST | `/api/auth/login` | Login |
| POST | `/api/auth/logout` | Logout |
| GET | `/api/auth/me` | Get Current User Info |
| GET | `/api/auth/providers` | Get Auth Providers |
| POST | `/api/auth/refresh` | Refresh Token |

## `configuration`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/configuration` | List Configuration |
| GET | `/api/configuration/branding` | Get Branding |
| POST | `/api/configuration/logo/upload` | Upload Logo |
| GET | `/api/configuration/product_types` | Get Product Types |
| GET | `/api/configuration/team_skills` | Get Team Skills |
| PUT | `/api/configuration/{key}` | Update Configuration |

## `dashboard`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/dashboard/stats` | Get Dashboard Stats |

## `document-types`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/document-types` | List Document Types |
| POST | `/api/document-types` | Create Document Type |
| GET | `/api/document-types/all` | List All Document Types |
| DELETE | `/api/document-types/{item_id}` | Delete Document Type |
| PUT | `/api/document-types/{item_id}` | Update Document Type |

## `dut-categories`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/dut-categories` | Get Dut Categories |

## `dut-types`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/dut-types` | List Dut Types |
| POST | `/api/dut-types` | Create Dut Type |
| POST | `/api/dut-types/reinit` | Reinit Dut Types |
| DELETE | `/api/dut-types/{dut_id}` | Delete Dut Type |
| PUT | `/api/dut-types/{dut_id}` | Update Dut Type |

## `estimations`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/estimations` | List Estimations |
| POST | `/api/estimations` | Create Estimation |
| POST | `/api/estimations/calculate` | Calculate Estimation Preview |
| POST | `/api/estimations/reinit` | Reinit Estimations |
| DELETE | `/api/estimations/{estimation_id}` | Delete Estimation |
| GET | `/api/estimations/{estimation_id}` | Get Estimation |
| PUT | `/api/estimations/{estimation_id}` | Update Estimation |
| POST | `/api/estimations/{estimation_id}/archive` | Archive Estimation To History |
| POST | `/api/estimations/{estimation_id}/assign` | Assign Estimation |
| POST | `/api/estimations/{estimation_id}/calculate` | Recalculate Estimation |
| POST | `/api/estimations/{estimation_id}/calibrate` | Calibrate Estimation |
| PUT | `/api/estimations/{estimation_id}/draft` | Update Draft Estimation |
| POST | `/api/estimations/{estimation_id}/export` | Export Estimation To External |
| POST | `/api/estimations/{estimation_id}/export-tasks` | Export Task Breakdown |
| POST | `/api/estimations/{estimation_id}/features` | Create Project Feature |
| GET | `/api/estimations/{estimation_id}/report/docx` | Download Word Report |
| GET | `/api/estimations/{estimation_id}/report/executive-summary` | Download Executive Summary |
| GET | `/api/estimations/{estimation_id}/report/pdf` | Download Pdf Report |
| GET | `/api/estimations/{estimation_id}/report/xlsx` | Download Excel Report |
| PUT | `/api/estimations/{estimation_id}/revise` | Revise Estimation |
| POST | `/api/estimations/{estimation_id}/send-report` | Send Estimation Report |
| POST | `/api/estimations/{estimation_id}/status` | Update Estimation Status |
| GET | `/api/estimations/{estimation_id}/versions` | Get Estimation Versions |
| GET | `/api/estimations/{estimation_id}/versions/{version_a}/diff/{version_b}` | Diff Estimation Versions |

## `feature-categories`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/feature-categories` | Get Feature Categories |

## `feature-presets`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/feature-presets` | List Feature Presets |
| POST | `/api/feature-presets` | Create Feature Preset |
| DELETE | `/api/feature-presets/{preset_id}` | Delete Feature Preset |
| PUT | `/api/feature-presets/{preset_id}` | Update Feature Preset |

## `features`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/features` | List Features |
| POST | `/api/features` | Create Feature |
| DELETE | `/api/features/{feature_id}` | Delete Feature |
| GET | `/api/features/{feature_id}` | Get Feature |
| PUT | `/api/features/{feature_id}` | Update Feature |
| POST | `/api/features/{feature_id}/promote` | Promote Feature To Global |
| POST | `/api/features/{feature_id}/request-promotion` | Request Feature Promotion |

## `healthcheck`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/healthcheck` | Healthcheck |

## `historical-projects`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/historical-projects` | List Historical Projects |
| POST | `/api/historical-projects` | Create Historical Project |
| DELETE | `/api/historical-projects/{project_id}` | Delete Historical Project |
| PUT | `/api/historical-projects/{project_id}` | Update Historical Project |

## `host-config`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/host-config` | Host Config |

## `import`

| Method | Path | Summary |
|--------|------|---------|
| POST | `/api/import/{entity_type}` | Execute Import Endpoint |
| POST | `/api/import/{entity_type}/preview` | Preview Import Endpoint |

## `integrations`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/integrations` | List Integrations |
| GET | `/api/integrations/JIRA/pr-items` | Get Jira Pr Items |
| POST | `/api/integrations/JIRA/pr-test-connection` | Test Jira Pr Connection |
| GET | `/api/integrations/SNIPE_IT/assets` | Get Snipeit Assets |
| GET | `/api/integrations/SNIPE_IT/categories` | Get Snipeit Categories |
| POST | `/api/integrations/outline/publish/{estimation_id}` | Publish To Outline |
| GET | `/api/integrations/outline/search` | Search Outline |
| GET | `/api/integrations/{system_name}` | Get Integration |
| PUT | `/api/integrations/{system_name}` | Update Integration |
| GET | `/api/integrations/{system_name}/status` | Integration Health |
| POST | `/api/integrations/{system_name}/sync` | Trigger Sync |
| POST | `/api/integrations/{system_name}/test` | Test Integration Endpoint |

## `notifications`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/notifications` | List Notifications |
| POST | `/api/notifications/mark-all-read` | Mark All Notifications Read |
| POST | `/api/notifications/test-email` | Test Email Connection |
| GET | `/api/notifications/unread-count` | Unread Notification Count |
| PUT | `/api/notifications/{notification_id}/read` | Mark Notification Read |

## `profiles`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/profiles` | List Profiles |
| POST | `/api/profiles` | Create Profile |
| DELETE | `/api/profiles/{profile_id}` | Delete Profile |
| PUT | `/api/profiles/{profile_id}` | Update Profile |

## `project-types`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/project-types` | Get Project Types |

## `public-holidays`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/public-holidays` | List Public Holidays |
| POST | `/api/public-holidays` | Create Public Holiday |
| POST | `/api/public-holidays/import-ics` | Import Holidays Ics |
| DELETE | `/api/public-holidays/{holiday_id}` | Delete Public Holiday |
| PUT | `/api/public-holidays/{holiday_id}` | Update Public Holiday |

## `reports`

| Method | Path | Summary |
|--------|------|---------|
| POST | `/api/reports/comparison` | Generate Comparison |
| GET | `/api/reports/trend` | Generate Trend |

## `requests`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/requests` | List Requests |
| POST | `/api/requests` | Create Request |
| POST | `/api/requests/reinit` | Reinit Request Inbox |
| GET | `/api/requests/{request_id}` | Get Request |
| PUT | `/api/requests/{request_id}` | Update Request |
| POST | `/api/requests/{request_id}/assign` | Assign Request |
| POST | `/api/requests/{request_id}/attachments` | Upload Attachment |
| GET | `/api/requests/{request_id}/detail` | Get Request Detail |

## `risk-items`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/risk-items` | List Risk Items |
| POST | `/api/risk-items` | Create Risk Item |
| DELETE | `/api/risk-items/{item_id}` | Delete Risk Item |
| PUT | `/api/risk-items/{item_id}` | Update Risk Item |

## `task-presets`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/task-presets` | List Task Presets |
| POST | `/api/task-presets` | Create Task Preset |
| DELETE | `/api/task-presets/{preset_id}` | Delete Task Preset |
| PUT | `/api/task-presets/{preset_id}` | Update Task Preset |

## `task-templates`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/task-templates` | List Task Templates |
| POST | `/api/task-templates` | Create Task Template |
| DELETE | `/api/task-templates/{template_id}` | Delete Task Template |
| PUT | `/api/task-templates/{template_id}` | Update Task Template |

## `team-members`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/team-members` | List Team Members |
| POST | `/api/team-members` | Create Team Member |
| DELETE | `/api/team-members/{member_id}` | Delete Team Member |
| PUT | `/api/team-members/{member_id}` | Update Team Member |

## `teams`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/teams` | List Teams |
| POST | `/api/teams` | Create Team |
| DELETE | `/api/teams/{team_id}` | Delete Team |
| PUT | `/api/teams/{team_id}` | Update Team |
| PUT | `/api/teams/{team_id}/members` | Update Team Members |

## `users`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/users` | List Users |
| POST | `/api/users` | Create User |
| GET | `/api/users/assignable` | List Assignable Users |
| DELETE | `/api/users/{user_id}` | Delete User |
| GET | `/api/users/{user_id}` | Get User |
| PUT | `/api/users/{user_id}` | Update User |

## `webhooks`

| Method | Path | Summary |
|--------|------|---------|
| POST | `/api/webhooks/redmine` | Redmine Webhook |

## `working-weeks`

| Method | Path | Summary |
|--------|------|---------|
| GET | `/api/working-weeks` | Calculate Working Weeks |
