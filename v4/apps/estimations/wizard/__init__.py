"""Estimation wizard pages (new estimation, draft resume, revision).

The wizard state is one ``EstimationCreate``-shaped dict kept in the Django
session (``state.py``); each step is a form partial that posts its fields,
is validated and merged into the state (``steps.py``); ``views.py`` renders
the steps and persists drafts through ``apps.estimations.services``.
"""
