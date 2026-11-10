import pytest


@pytest.fixture
def web_login(client):
    """Sign a user into the Django test client (session cookie)."""
    def _login(user):
        client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")
        return client
    return _login


@pytest.fixture
def admin_client(web_login, admin_user):
    return web_login(admin_user)
