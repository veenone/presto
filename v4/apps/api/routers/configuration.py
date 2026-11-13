"""API routes: /configuration/*, /dut-categories, /feature-categories, /project-types.

Django resolves the path before the method, so the literal GET paths
(``/configuration/branding``, ``/product_types``, ``/team_skills``) also carry
a hidden PUT that behaves like ``PUT /configuration/{key}``. The literal
paths are declared before the parameterised one so they match first.
"""

from ninja import File, Router, UploadedFile

from apps.api.auth import authenticated, role
from apps.api.schemas.configuration import (
    BrandingOut,
    ConfigurationOut,
    ConfigurationUpdate,
    LogoUploadOut,
)
from apps.core import services

from .catalog import call

router = Router(auth=authenticated, tags=["configuration"])


def _put(key: str, data: ConfigurationUpdate):
    return services.update_configuration(key, data.value)


@router.get("/configuration/branding", response=BrandingOut, auth=None)
def get_branding(request):
    """Return branding config (logo) without authentication."""
    return services.get_branding()


@router.put("/configuration/branding", response=ConfigurationOut, auth=role("ADMIN"), include_in_schema=False)
def update_branding_key(request, data: ConfigurationUpdate):
    return _put("branding", data)


@router.get("/configuration/product_types", response=list[str], auth=None)
def get_product_types(request):
    """Return the configured product types as a list of strings."""
    return services.product_types()


@router.put("/configuration/product_types", response=ConfigurationOut, auth=role("ADMIN"),
            include_in_schema=False)
def update_product_types(request, data: ConfigurationUpdate):
    return _put("product_types", data)


@router.get("/configuration/team_skills", response=list[str], auth=None)
def get_team_skills(request):
    """Return the configured team skills as a list of strings."""
    return services.team_skills()


@router.put("/configuration/team_skills", response=ConfigurationOut, auth=role("ADMIN"),
            include_in_schema=False)
def update_team_skills(request, data: ConfigurationUpdate):
    return _put("team_skills", data)


@router.get("/configuration", response=list[ConfigurationOut])
def list_configuration(request):
    return services.list_configuration()


@router.post("/configuration/logo/upload", response=LogoUploadOut, auth=role("ADMIN"))
def upload_logo(request, file: File[UploadedFile]):
    """Upload a logo image and update the logo_url configuration."""
    return call(services.save_logo, file.name, file.read())


@router.put("/configuration/{key}", response=ConfigurationOut, auth=role("ADMIN"))
def update_configuration(request, key: str, data: ConfigurationUpdate):
    return _put(key, data)


@router.get("/dut-categories", response=list[str])
def get_dut_categories(request):
    """Return the configured DUT categories as a list of strings."""
    return services.dut_categories()


@router.get("/feature-categories", response=list[str])
def get_feature_categories(request):
    """Return the configured feature categories as a list of strings."""
    return services.feature_categories()


@router.get("/project-types", response=list[str])
def get_project_types(request):
    """Return the configured project types as a list of strings."""
    return services.project_types()
