"""Permission resolver: role = preset, permissions = keys, explicit false wins.

Stored per (account, user) in ``app.account_access``: a role label plus a JSON
object of explicit overrides. Resolution order for a key:

1. role ``owner``            -> allowed everywhere, the JSON is not even read;
2. explicit ``false`` in JSON -> denied (it overrides the preset);
3. explicit ``true``  in JSON -> allowed (even if the preset lacks it);
4. key absent                -> the role preset decides.

So "a buyer who may also see sales" or "a manager without ads" are just
different JSON, not new roles.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

SECTION_SALES: Final = "section.sales"
SECTION_ADS: Final = "section.ads"
SECTION_PURCHASE: Final = "section.purchase"
SECTION_SETTINGS: Final = "section.settings"
SECTION_TEAM: Final = "section.team"

OWNER: Final = "owner"

PRESETS: Final[dict[str, frozenset[str]]] = {
    OWNER: frozenset(),  # never consulted: the owner passes everything
    "manager": frozenset({SECTION_SALES, SECTION_ADS, SECTION_PURCHASE}),
    "buyer": frozenset({SECTION_PURCHASE}),
    "viewer": frozenset({SECTION_SALES}),
}


def has_perm(role: str, perms: Mapping[str, Any] | None, key: str) -> bool:
    if role == OWNER:
        return True
    if role not in PRESETS:
        return False  # unknown role: deny rather than guess
    explicit = (perms or {}).get(key)
    if explicit is False:
        return False
    if explicit is True:
        return True
    # Anything else (missing, null, "yes", 1) is not an explicit decision -> preset.
    return key in PRESETS[role]


def effective_perms(role: str, perms: Mapping[str, Any] | None, keys: list[str]) -> dict[str, bool]:
    return {k: has_perm(role, perms, k) for k in keys}
