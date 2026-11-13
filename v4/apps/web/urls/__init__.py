"""Web page URL modules, one per page group.

``presto/urls.py`` includes every module listed in ``MODULES``. Each module
exposes ``urlpatterns``; any navigation item whose URL name is not registered
by a module falls back to the "being rebuilt" placeholder.
"""

MODULES = [
    "apps.web.urls.profiles",
    "apps.web.urls.features",       # features, feature presets, task templates (+ task presets)
    "apps.web.urls.registries",     # DUTs, DUT assets, document types, risks, PR registry
    "apps.web.urls.planning",       # historical projects, team, public holidays
    "apps.web.urls.admin_people",   # users, RBAC, audit log
    "apps.web.urls.admin_system",   # settings, backup & restore
    "apps.web.urls.estimation_pages",   # estimations list, estimation detail
    "apps.web.urls.estimation_wizard",  # new estimation, draft resume, edit / revise
    "apps.web.urls.dashboard",      # dashboard
    "apps.web.urls.inbox",          # request inbox list and detail
    "apps.web.urls.integrations",   # integrations config, notifications page
]
