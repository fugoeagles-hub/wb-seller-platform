"""Fixtures. Needs a running database: scripts/run_tests.sh starts one and sets the DSNs.

RLS_ADMIN_DSN  superuser connection, used only to seed and to inspect the catalog
RLS_APP_DSN    app_user connection, the one the application would use
"""

from __future__ import annotations

import os

import psycopg
import pytest

ADMIN_DSN = os.environ.get("RLS_ADMIN_DSN")
APP_DSN = os.environ.get("RLS_APP_DSN")

pytestmark_db = pytest.mark.skipif(not (ADMIN_DSN and APP_DSN), reason="RLS_ADMIN_DSN / RLS_APP_DSN not set")


def pytest_collection_modifyitems(config, items):
    if ADMIN_DSN and APP_DSN:
        return
    skip = pytest.mark.skip(reason="no database: run scripts/run_tests.sh")
    for item in items:
        if "db" in item.fixturenames:
            item.add_marker(skip)


@pytest.fixture
def admin():
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        yield conn


@pytest.fixture
def db(admin):
    """Reset and seed two tenants (as superuser, who bypasses RLS), return their ids."""
    admin.execute("TRUNCATE app.daily_sales, app.account_access, app.stores, app.accounts RESTART IDENTITY CASCADE")
    ids = {}
    for name in ("alpha", "beta"):
        acc = admin.execute("INSERT INTO app.accounts (name) VALUES (%s) RETURNING id", (name,)).fetchone()[0]
        store = admin.execute(
            "INSERT INTO app.stores (account_id, name) VALUES (%s, %s) RETURNING id", (acc, f"{name}-store")
        ).fetchone()[0]
        for day, sku, orders in (("2026-01-01", "sku-1", 3), ("2026-01-02", "sku-2", 5)):
            admin.execute(
                "INSERT INTO app.daily_sales VALUES (%s, %s, %s, %s, %s, %s)",
                (acc, store, day, sku, orders, orders * 100),
            )
        admin.execute(
            "INSERT INTO app.account_access VALUES (%s, %s, 'manager', %s)",
            (acc, 1000 + acc, '{"section.ads": false, "section.settings": true}'),
        )
        ids[name] = {"account": acc, "store": store}
    return ids


@pytest.fixture
def app():
    """Connection as the application role (no superuser, no BYPASSRLS)."""
    with psycopg.connect(APP_DSN) as conn:
        yield conn
