import pytest

from rls_demo import PRESETS, has_perm
from rls_demo.db import tenant_tx
from rls_demo.perms import SECTION_ADS, SECTION_PURCHASE, SECTION_SALES, SECTION_SETTINGS, SECTION_TEAM


def test_preset_decides_when_json_is_empty():
    assert has_perm("buyer", {}, SECTION_PURCHASE)
    assert not has_perm("buyer", {}, SECTION_SALES)
    assert has_perm("viewer", None, SECTION_SALES)


def test_explicit_false_overrides_preset():
    assert has_perm("manager", {}, SECTION_ADS)
    assert not has_perm("manager", {SECTION_ADS: False}, SECTION_ADS)


def test_explicit_true_grants_beyond_preset():
    assert not has_perm("buyer", {}, SECTION_SALES)
    assert has_perm("buyer", {SECTION_SALES: True}, SECTION_SALES)


def test_owner_passes_everywhere_and_json_is_ignored():
    for key in (SECTION_ADS, SECTION_SETTINGS, SECTION_TEAM, "section.never.heard.of"):
        assert has_perm("owner", {key: False}, key)


def test_non_boolean_values_fall_back_to_preset():
    assert has_perm("manager", {SECTION_ADS: None}, SECTION_ADS)
    assert not has_perm("viewer", {SECTION_ADS: "yes"}, SECTION_ADS)
    assert not has_perm("viewer", {SECTION_ADS: 1}, SECTION_ADS)


def test_unknown_role_is_denied():
    assert not has_perm("intern", {SECTION_SALES: True}, SECTION_SALES)


def test_only_owner_has_empty_preset():
    assert [r for r, keys in PRESETS.items() if not keys] == ["owner"]


@pytest.mark.parametrize("who", ["alpha", "beta"])
def test_perms_round_trip_through_jsonb_under_rls(db, app, who):
    """perms come back from account_access (RLS-scoped) and feed the resolver."""
    with tenant_tx(app, db[who]["account"]) as tx:
        role, perms = tx.execute("SELECT role, perms FROM app.account_access").fetchone()
    assert role == "manager"
    assert perms == {"section.ads": False, "section.settings": True}
    assert not has_perm(role, perms, SECTION_ADS)
    assert has_perm(role, perms, SECTION_SETTINGS)
    assert has_perm(role, perms, SECTION_SALES)
    assert not has_perm(role, perms, SECTION_TEAM)
