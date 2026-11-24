"""Pin a connection to one tenant for exactly one transaction."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.pq import TransactionStatus


@contextmanager
def tenant_tx(conn: psycopg.Connection, account_id: int) -> Iterator[psycopg.Connection]:
    """Open a transaction in which ``app.current_account_id`` is ``account_id``.

    ``set_config(..., is_local => true)`` is the function form of ``SET LOCAL``: the
    value lives until COMMIT/ROLLBACK and then disappears. That is what makes it safe
    with connection pools: a connection returned to the pool carries no tenant, so
    the next borrower starts from "no tenant" and sees nothing until it sets one.

    ``account_id`` goes through ``int()`` and then a bound parameter, never string formatting.
    """
    account_id = int(account_id)
    if conn.info.transaction_status != TransactionStatus.IDLE:
        # Inside an open transaction `conn.transaction()` would be only a savepoint,
        # and the setting would outlive it. Refuse instead of leaking a tenant.
        raise RuntimeError("tenant_tx must start on an idle connection (no open transaction)")
    with conn.transaction():
        conn.execute("SELECT set_config('app.current_account_id', %s, true)", (str(account_id),))
        yield conn
