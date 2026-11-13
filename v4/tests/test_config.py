import pytest

from apps.core import config
from apps.core.models import Configuration

pytestmark = pytest.mark.django_db


def put(key, value):
    Configuration.objects.update_or_create(key=key, defaults={"value": value})


def test_typed_getters():
    for key, value in {
        "f": "0.5", "i": "7.0", "b": "true", "b0": "false", "csv": "SIM, eSIM,,UICC",
        "arr": '["Payment", "Telco"]', "js": '{"ADMIN": ["x"]}',
    }.items():
        put(key, value)
    assert config.get_float("f") == 0.5
    assert config.get_int("i") == 7
    assert config.get_bool("b") is True and config.get_bool("b0") is False
    assert config.get_list("csv") == ["SIM", "eSIM", "UICC"]
    assert config.get_list("arr") == ["Payment", "Telco"]
    assert config.get_json("js") == {"ADMIN": ["x"]}


def test_defaults_for_missing_blank_and_invalid():
    put("blank", "")
    put("bad", "abc")
    assert config.get_float("missing", 1.5) == 1.5
    assert config.get_float("blank", 2.0) == 2.0
    assert config.get_float("bad", 3.0) == 3.0
    assert config.get_list("missing") == []
    assert config.get_json("bad", {}) == {}
    assert config.get_str("missing", "d") == "d"


def test_set_value_stores_v3_string_formats():
    config.set_value("flag", True)
    config.set_value("ids", [1, 2])
    config.set_value("n", 4.0, description="hours")
    assert Configuration.objects.get(key="flag").value == "true"
    assert Configuration.objects.get(key="ids").value == "[1, 2]"
    obj = Configuration.objects.get(key="n")
    assert obj.value == "4.0" and obj.description == "hours"


def test_ensure_defaults_is_idempotent_and_keeps_values():
    put("buffer_percentage", "15")
    created = config.ensure_defaults()
    assert created == len(config.load_defaults())  # all others + schema_version marker
    assert Configuration.objects.get(key="buffer_percentage").value == "15"
    assert Configuration.objects.get(key="schema_version").value == config.DJANGO_SCHEMA_MARKER
    assert config.ensure_defaults() == 0
