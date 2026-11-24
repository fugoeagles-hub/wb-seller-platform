"""The three guarantees of the demo, plus checks that the setup itself is sound."""

import psycopg
import pytest
from psycopg import errors

from rls_demo import tenant_tx

TABLES = ("accounts", "stores", "account_access", "daily_sales")


# ---- 1. tenant A sees only its own rows -----------------------------------------------


@pytest.mark.parametrize("who", ["alpha", "beta"])
def test_tenant_sees_only_own_rows(db, app, who):
    me = db[who]["account"]
    with tenant_tx(app, me) as tx:
        sales = tx.execute("SELECT DISTINCT account_id FROM app.daily_sales").fetchall()
        count = tx.execute("SELECT count(*) FROM app.daily_sales").fetchone()[0]
        stores = tx.execute("SELECT account_id FROM app.stores").fetchall()
        accounts = tx.execute("SELECT id FROM app.accounts").fetchall()
        access = tx.execute("SELECT account_id FROM app.account_access").fetchall()
    assert sales == [(me,)] and count == 2
    assert stores == [(me,)]
    assert accounts == [(me,)]
    assert access == [(me,)]


def test_explicit_filter_for_foreign_tenant_returns_nothing(db, app):
    other = db["beta"]["account"]
    with tenant_tx(app, db["alpha"]["account"]) as tx:
        rows = tx.execute("SELECT * FROM app.daily_sales WHERE account_id = %s", (other,)).fetchall()
    assert rows == []


def test_foreign_rows_cannot_be_updated_or_deleted(db, app, admin):
    with tenant_tx(app, db["alpha"]["account"]) as tx:
        upd = tx.execute("UPDATE app.daily_sales SET orders = 999 WHERE account_id = %s", (db["beta"]["account"],))
        assert upd.rowcount == 0
        dele = tx.execute("DELETE FROM app.stores WHERE account_id = %s", (db["beta"]["account"],))
        assert dele.rowcount == 0
    still = admin.execute("SELECT sum(orders), count(*) FROM app.daily_sales WHERE account_id = %s",
                          (db["beta"]["account"],)).fetchone()
    assert still == (8, 2)


# ---- 2. writing into another tenant is rejected ---------------------------------------


def test_insert_for_other_tenant_is_rejected(db, app):
    a, b = db["alpha"], db["beta"]
    with pytest.raises(errors.InsufficientPrivilege, match="row-level security"):
        with tenant_tx(app, a["account"]) as tx:
            tx.execute(
                "INSERT INTO app.daily_sales VALUES (%s, %s, '2026-02-01', 'x', 1, 1)",
                (b["account"], b["store"]),
            )


def test_update_cannot_move_row_to_other_tenant(db, app):
    a, b = db["alpha"], db["beta"]
    with pytest.raises(errors.InsufficientPrivilege, match="row-level security"):
        with tenant_tx(app, a["account"]) as tx:
            tx.execute("UPDATE app.stores SET account_id = %s WHERE id = %s", (b["account"], a["store"]))


def test_own_insert_works_and_is_visible(db, app):
    a = db["alpha"]
    with tenant_tx(app, a["account"]) as tx:
        tx.execute("INSERT INTO app.daily_sales VALUES (%s, %s, '2026-02-01', 'new', 2, 20)", (a["account"], a["store"]))
    with tenant_tx(app, a["account"]) as tx:
        assert tx.execute("SELECT count(*) FROM app.daily_sales").fetchone()[0] == 3


def test_child_row_cannot_reference_foreign_store(db, app):
    """Composite FK: even with a valid own account_id, a foreign store_id is refused."""
    a, b = db["alpha"], db["beta"]
    with pytest.raises(errors.ForeignKeyViolation):
        with tenant_tx(app, a["account"]) as tx:
            tx.execute(
                "INSERT INTO app.daily_sales VALUES (%s, %s, '2026-02-01', 'x', 1, 1)",
                (a["account"], b["store"]),
            )


# ---- 3. no tenant set -> zero rows (fail closed) --------------------------------------


@pytest.mark.parametrize("table", TABLES)
def test_without_setting_nothing_is_visible(db, app, table):
    assert app.execute(f"SELECT count(*) FROM app.{table}").fetchone()[0] == 0
    app.rollback()


def test_without_setting_writes_are_rejected(db, app):
    a = db["alpha"]
    with pytest.raises(errors.InsufficientPrivilege):
        app.execute("INSERT INTO app.daily_sales VALUES (%s, %s, '2026-02-01', 'x', 1, 1)", (a["account"], a["store"]))
    app.rollback()


def test_setting_does_not_leak_after_transaction(db, app):
    with tenant_tx(app, db["alpha"]["account"]) as tx:
        assert tx.execute("SELECT count(*) FROM app.daily_sales").fetchone()[0] == 2
    # same connection, new transaction, nobody asked for a tenant -> empty
    assert app.execute("SELECT count(*) FROM app.daily_sales").fetchone()[0] == 0
    assert app.execute("SELECT current_setting('app.current_account_id', true)").fetchone()[0] in ("", None)
    app.rollback()


def test_empty_setting_is_treated_as_no_tenant(db, app):
    with app.transaction():
        app.execute("SELECT set_config('app.current_account_id', '', true)")
        assert app.execute("SELECT count(*) FROM app.daily_sales").fetchone()[0] == 0


def test_garbage_setting_errors_instead_of_leaking(db, app):
    with pytest.raises(errors.InvalidTextRepresentation):
        with app.transaction():
            app.execute("SELECT set_config('app.current_account_id', 'abc', true)")
            app.execute("SELECT * FROM app.daily_sales")


def test_unknown_tenant_sees_nothing(db, app):
    with tenant_tx(app, 987654) as tx:
        assert tx.execute("SELECT count(*) FROM app.daily_sales").fetchone()[0] == 0


# ---- helper behaviour -----------------------------------------------------------------


def test_tenant_tx_refuses_open_transaction(db, app):
    app.execute("SELECT 1")  # opens an implicit transaction
    with pytest.raises(RuntimeError):
        with tenant_tx(app, db["alpha"]["account"]):
            pass
    app.rollback()


def test_tenant_tx_rejects_non_integer_id(db, app):
    with pytest.raises(ValueError):
        with tenant_tx(app, "1; DROP TABLE app.accounts"):
            pass


# ---- the setup itself -----------------------------------------------------------------


def test_app_role_cannot_bypass_rls(admin):
    row = admin.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'app_user'").fetchone()
    assert row == (False, False)


def test_every_table_has_rls_enabled_and_forced(admin):
    rows = admin.execute(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'app' AND c.relkind = 'r'"
    ).fetchall()
    assert {r[0] for r in rows} == set(TABLES)
    assert all(r[1] and r[2] for r in rows), rows


def test_force_applies_to_table_owner_too(db, admin):
    """FORCE is what stops the owner role from seeing everything. Prove it by removing it."""
    with admin.transaction():
        admin.execute("SET LOCAL ROLE schema_owner")
        assert admin.execute("SELECT count(*) FROM app.daily_sales").fetchone()[0] == 0
        admin.execute("ALTER TABLE app.daily_sales NO FORCE ROW LEVEL SECURITY")
        assert admin.execute("SELECT count(*) FROM app.daily_sales").fetchone()[0] == 4  # owner sees all
        raise psycopg.Rollback()  # undo the ALTER


def test_force_was_restored_after_experiment(admin):
    assert admin.execute(
        "SELECT relforcerowsecurity FROM pg_class WHERE oid = 'app.daily_sales'::regclass"
    ).fetchone()[0] is True
