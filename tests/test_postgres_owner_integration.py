"""Opt-in PostgreSQL 18 tests using an independently prepared test endpoint."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine

import dbtalk.postgres.owner_transfer as transfer
from dbtalk.cli import cli
from dbtalk.database.dsn import ParsedDsn, parse_dsn
from dbtalk.database.models import DatabaseOperationError
from dbtalk.postgres.owner import reassign_owned
from dbtalk.postgres.owner_transfer import transfer_owned

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class IsolatedDatabase:
    engine: Engine
    parsed: ParsedDsn
    database: str
    source: str
    target: str
    administrator: str

    def role_dsn(self, name: str) -> ParsedDsn:
        return parse_dsn(self.parsed.url.set(username=name).render_as_string(hide_password=False))


@pytest.fixture
def postgres_test() -> Iterator[IsolatedDatabase]:
    port_value = os.environ.get("DBTALK_OWNER_TEST_PORT")
    if port_value is None:
        pytest.skip("set DBTALK_OWNER_TEST_PORT to an isolated PostgreSQL 18 trust-auth endpoint")
    token = uuid.uuid4().hex[:10]
    database = f"owner_test_{token}"
    source, target, administrator = (
        f"owner_{kind}_{token}" for kind in ("source", "target", "admin")
    )
    url = URL.create(
        "postgresql+psycopg",
        username="postgres",
        host="127.0.0.1",
        port=int(port_value),
        database="postgres",
    )
    admin_engine = create_engine(url, isolation_level="AUTOCOMMIT")
    engine: Engine | None = None
    try:
        with admin_engine.connect() as connection:
            assert int(connection.exec_driver_sql("SHOW server_version_num").scalar_one()) >= 180000
            connection.exec_driver_sql(f"CREATE ROLE {source} LOGIN")
            connection.exec_driver_sql(f"CREATE ROLE {target} LOGIN")
            connection.exec_driver_sql(f"CREATE ROLE {administrator} LOGIN SUPERUSER")
            connection.exec_driver_sql(f"CREATE DATABASE {database}")
            connection.exec_driver_sql(f"GRANT CREATE ON DATABASE {database} TO {source}, {target}")
        parsed = parse_dsn(
            url.set(database=database, username=administrator).render_as_string(hide_password=False)
        )
        engine = create_engine(parsed.url)
        yield IsolatedDatabase(engine, parsed, database, source, target, administrator)
    finally:
        if engine is not None:
            engine.dispose()
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {database}")
            connection.exec_driver_sql(f"DROP ROLE IF EXISTS {source}, {target}, {administrator}")
        admin_engine.dispose()


def test_all_supported_kinds_and_automatic_objects_transfer(
    postgres_test: IsolatedDatabase,
) -> None:
    db = postgres_test
    with db.engine.begin() as connection:
        connection.exec_driver_sql("SET ROLE postgres")
        connection.exec_driver_sql("CREATE SCHEMA business")
        connection.exec_driver_sql(f"CREATE SCHEMA sentinel AUTHORIZATION {db.source}")
        connection.exec_driver_sql("CREATE EXTENSION hstore WITH SCHEMA business")
        for statement in (
            "CREATE TYPE business.state AS ENUM ('open', 'closed')",
            "CREATE DOMAIN business.positive AS integer CHECK (VALUE > 0)",
            "CREATE TYPE business.pair AS (a integer, b text)",
            "CREATE TYPE business.int_span AS RANGE (subtype = integer)",
            'CREATE TABLE business."Order Items" '
            "(id bigserial PRIMARY KEY, payload text, state business.state)",
            'INSERT INTO business."Order Items" (payload, state) '
            "VALUES (repeat('value', 10000), 'open')",
            "CREATE TABLE business.identity_table "
            "(id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY)",
            "CREATE TABLE business.partitioned (id integer) PARTITION BY RANGE (id)",
            "CREATE TABLE business.child PARTITION OF business.partitioned "
            "FOR VALUES FROM (0) TO (100)",
            "CREATE SEQUENCE business.standalone_seq",
            'CREATE VIEW business.items_view AS SELECT id FROM business."Order Items"',
            'CREATE MATERIALIZED VIEW business.items_mat AS SELECT id FROM business."Order Items"',
            "CREATE FUNCTION business.overload(integer) RETURNS integer "
            "LANGUAGE sql AS 'SELECT $1'",
            "CREATE FUNCTION business.overload(text) RETURNS text LANGUAGE sql AS 'SELECT $1'",
            "CREATE PROCEDURE business.noop() LANGUAGE plpgsql AS 'BEGIN NULL; END'",
            "CREATE AGGREGATE business.total(integer) "
            "(SFUNC = int4pl, STYPE = integer, INITCOND = '0')",
            "CREATE FUNCTION business.finish(integer, integer) RETURNS integer "
            "LANGUAGE sql AS 'SELECT $1 + $2'",
            "CREATE AGGREGATE business.ordered_total(integer ORDER BY integer) "
            "(SFUNC = int4pl, STYPE = integer, FINALFUNC = business.finish, INITCOND = '0')",
            "CREATE FOREIGN DATA WRAPPER fixture_fdw",
            "CREATE SERVER fixture_server FOREIGN DATA WRAPPER fixture_fdw",
            "CREATE FOREIGN TABLE business.foreign_items (id integer) SERVER fixture_server",
        ):
            connection.exec_driver_sql(statement)
        database_owner = connection.exec_driver_sql(
            "SELECT datdba FROM pg_catalog.pg_database WHERE datname = current_database()"
        ).scalar_one()
        shared_owners = connection.exec_driver_sql(
            "SELECT oid, spcowner FROM pg_catalog.pg_tablespace ORDER BY oid"
        ).all()

    preview = transfer_owned(db.parsed, db.database, "postgres", db.target, schemas=("business",))
    assert not preview.blocked, preview.blocked
    assert {obj.kind for obj in preview.objects} >= {
        "schema",
        "table",
        "partitioned table",
        "foreign table",
        "view",
        "materialized view",
        "sequence",
        "function",
        "procedure",
        "aggregate",
        "enum",
        "domain",
        "composite",
        "range",
    }
    assert {obj.kind for obj in preview.automatic} >= {
        "index",
        "toast",
        "composite",
        "base type",
        "multirange",
        "sequence",
    }
    assert preview.excluded
    with db.engine.connect() as connection:
        for obj in preview.objects:
            actual = transfer._read_object(connection, obj)
            assert actual is not None and actual.owner_oid == 10

    result = transfer_owned(
        db.parsed, db.database, "postgres", db.target, schemas=("business",), execute=True
    )
    with db.engine.connect() as connection:
        for obj in (*result.objects, *result.automatic):
            actual = transfer._read_object(connection, obj)
            assert actual is not None and actual.owner_oid == result.target_oid, obj
        for obj in result.excluded:
            actual = transfer._read_object(connection, obj)
            assert actual is not None and actual.owner_oid == obj.owner_oid, obj
        assert (
            connection.exec_driver_sql(
                "SELECT nspowner FROM pg_catalog.pg_namespace WHERE nspname = 'pg_catalog'"
            ).scalar_one()
            == 10
        )
        assert (
            connection.exec_driver_sql(
                "SELECT datdba FROM pg_catalog.pg_database WHERE datname = current_database()"
            ).scalar_one()
            == database_owner
        )
        assert (
            connection.exec_driver_sql(
                "SELECT oid, spcowner FROM pg_catalog.pg_tablespace ORDER BY oid"
            ).all()
            == shared_owners
        )
        connection.exec_driver_sql(f"SET ROLE {db.target}")
        assert (
            connection.exec_driver_sql(
                'SELECT length(payload) FROM business."Order Items"'
            ).scalar_one()
            == 50000
        )
        assert connection.exec_driver_sql("SELECT business.overload(5)").scalar_one() == 5
    again = transfer_owned(
        db.parsed, db.database, "postgres", db.target, schemas=("business",), execute=True
    )
    assert not again.objects and not again.blocked


def test_renamed_initialization_role_and_user_superuser_are_distinguished(
    postgres_test: IsolatedDatabase,
) -> None:
    db = postgres_test
    renamed = f"bootstrap_{uuid.uuid4().hex[:10]}"
    with db.engine.begin() as connection:
        connection.exec_driver_sql("SET ROLE postgres")
        connection.exec_driver_sql("CREATE TABLE public.items (id integer)")
        connection.exec_driver_sql("RESET ROLE")
        connection.exec_driver_sql(f"ALTER ROLE postgres RENAME TO {renamed}")
    try:
        with pytest.raises(DatabaseOperationError, match="initialization/system role"):
            reassign_owned(db.parsed, db.database, renamed, db.target)
        result = transfer_owned(
            db.parsed, db.database, renamed, db.target, schemas=("public",), execute=True
        )
        assert result.objects
        reassign_owned(db.parsed, db.database, db.administrator, db.target, execute=True)
    finally:
        with db.engine.begin() as connection:
            connection.exec_driver_sql(f"ALTER ROLE {renamed} RENAME TO postgres")


def test_database_scope_lists_unsupported_objects_and_schema_scope_omits_them(
    postgres_test: IsolatedDatabase,
) -> None:
    db = postgres_test
    with db.engine.begin() as connection:
        connection.exec_driver_sql("SET ROLE postgres")
        connection.exec_driver_sql("CREATE TABLE public.items (id integer)")
        connection.exec_driver_sql("CREATE PUBLICATION fixture_publication")
    preview = transfer_owned(db.parsed, db.database, "postgres", db.target)
    assert any(obj.kind == "publication" for obj in preview.blocked)
    with pytest.raises(DatabaseOperationError, match="not supported"):
        transfer_owned(db.parsed, db.database, "postgres", db.target, execute=True)
    with db.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT relowner FROM pg_catalog.pg_class WHERE oid = 'public.items'::regclass"
            ).scalar_one()
            == 10
        )
    result = transfer_owned(
        db.parsed, db.database, "postgres", db.target, schemas=("public",), execute=True
    )
    assert not result.blocked


def test_extension_member_owned_sequence_blocks_parent_transfer(
    postgres_test: IsolatedDatabase,
) -> None:
    db = postgres_test
    with db.engine.begin() as connection:
        connection.exec_driver_sql("SET ROLE postgres")
        connection.exec_driver_sql("CREATE EXTENSION hstore")
        connection.exec_driver_sql("CREATE TABLE public.items (id serial PRIMARY KEY)")
        connection.exec_driver_sql("ALTER EXTENSION hstore ADD SEQUENCE public.items_id_seq")
    preview = transfer_owned(db.parsed, db.database, "postgres", db.target, schemas=("public",))
    assert any("would change an extension member" in obj.reason for obj in preview.blocked)
    with pytest.raises(DatabaseOperationError, match="extension member"):
        transfer_owned(
            db.parsed, db.database, "postgres", db.target, schemas=("public",), execute=True
        )
    with db.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT relowner FROM pg_catalog.pg_class WHERE oid = 'public.items'::regclass"
            ).scalar_one()
            == 10
        )


def test_missing_target_schema_permission_fails_before_alter(
    postgres_test: IsolatedDatabase,
) -> None:
    db = postgres_test
    with db.engine.begin() as connection:
        connection.exec_driver_sql("CREATE SCHEMA business")
        connection.exec_driver_sql(f"GRANT USAGE, CREATE ON SCHEMA business TO {db.source}")
        connection.exec_driver_sql(f"GRANT {db.target} TO {db.source}")
        connection.exec_driver_sql(f"SET ROLE {db.source}")
        connection.exec_driver_sql("CREATE TABLE business.items (id integer)")
    with pytest.raises(
        DatabaseOperationError, match="target role requires CREATE on schema business"
    ):
        transfer_owned(
            db.role_dsn(db.source),
            db.database,
            db.source,
            db.target,
            schemas=("business",),
            execute=True,
        )
    with db.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT pg_get_userbyid(relowner) FROM pg_catalog.pg_class "
                "WHERE oid = 'business.items'::regclass"
            ).scalar_one()
            == db.source
        )


def test_ddl_failure_rolls_back_preceding_owner_changes(postgres_test: IsolatedDatabase) -> None:
    db = postgres_test
    with db.engine.begin() as connection:
        connection.exec_driver_sql("SET ROLE postgres")
        connection.exec_driver_sql("CREATE TABLE public.a_first (id integer)")
        connection.exec_driver_sql("CREATE TABLE public.z_last (id integer)")
        connection.exec_driver_sql("RESET ROLE")
        connection.exec_driver_sql("""
            CREATE FUNCTION public.reject_last() RETURNS event_trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_event_trigger_ddl_commands()
                           WHERE object_identity = 'public.z_last') THEN
                    RAISE EXCEPTION 'fixture refuses final ALTER';
                END IF;
            END; $$
        """)
        connection.exec_driver_sql(
            "CREATE EVENT TRIGGER reject_last ON ddl_command_end "
            "WHEN TAG IN ('ALTER TABLE') EXECUTE FUNCTION public.reject_last()"
        )
    with pytest.raises(DatabaseOperationError, match="z_last.*fixture refuses final ALTER"):
        transfer_owned(
            db.parsed, db.database, "postgres", db.target, schemas=("public",), execute=True
        )
    with db.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT count(*) FROM pg_catalog.pg_class "
                "WHERE oid IN ('public.a_first'::regclass, 'public.z_last'::regclass) "
                "AND relowner = 10"
            ).scalar_one()
            == 2
        )


def test_extension_membership_change_during_ddl_is_rolled_back(
    postgres_test: IsolatedDatabase,
) -> None:
    db = postgres_test
    with db.engine.begin() as connection:
        connection.exec_driver_sql("SET ROLE postgres")
        connection.exec_driver_sql("CREATE EXTENSION hstore")
        connection.exec_driver_sql("CREATE TABLE public.items (id integer)")
        connection.exec_driver_sql("RESET ROLE")
        connection.exec_driver_sql("""
            CREATE FUNCTION public.protect_items() RETURNS event_trigger LANGUAGE plpgsql AS $$
            BEGIN
                ALTER EXTENSION hstore ADD TABLE public.items;
            END; $$
        """)
        connection.exec_driver_sql(
            "CREATE EVENT TRIGGER protect_items ON ddl_command_end "
            "WHEN TAG IN ('ALTER TABLE') EXECUTE FUNCTION public.protect_items()"
        )
    with pytest.raises(DatabaseOperationError, match="ownership result does not match plan"):
        transfer_owned(
            db.parsed, db.database, "postgres", db.target, schemas=("public",), execute=True
        )
    with db.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT relowner FROM pg_catalog.pg_class WHERE oid = 'public.items'::regclass"
            ).scalar_one()
            == 10
        )
        assert (
            connection.exec_driver_sql(
                "SELECT count(*) FROM pg_catalog.pg_depend "
                "WHERE classid = 'pg_class'::regclass AND objid = 'public.items'::regclass "
                "AND deptype = 'e'"
            ).scalar_one()
            == 0
        )


def test_non_superuser_can_transfer_a_schema_and_its_objects(
    postgres_test: IsolatedDatabase,
) -> None:
    db = postgres_test
    with db.engine.begin() as connection:
        connection.exec_driver_sql(f"GRANT {db.target} TO {db.source}")
        connection.exec_driver_sql(f"CREATE SCHEMA business AUTHORIZATION {db.source}")
        connection.exec_driver_sql(f"SET ROLE {db.source}")
        connection.exec_driver_sql("CREATE TABLE business.items (id serial PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO business.items DEFAULT VALUES")
    result = transfer_owned(
        db.role_dsn(db.source),
        db.database,
        db.source,
        db.target,
        schemas=("business",),
        execute=True,
    )
    assert not result.blocked
    with db.engine.connect() as connection:
        connection.exec_driver_sql(f"SET ROLE {db.target}")
        assert connection.exec_driver_sql("SELECT count(*) FROM business.items").scalar_one() == 1


def test_cli_uses_dotenv_and_repeated_schema_options(
    postgres_test: IsolatedDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = postgres_test
    with db.engine.begin() as connection:
        connection.exec_driver_sql("SET ROLE postgres")
        connection.exec_driver_sql("CREATE SCHEMA business")
        connection.exec_driver_sql("CREATE TABLE business.items (id integer)")
        connection.exec_driver_sql("CREATE TABLE public.items (id integer)")
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "DBTALK_DSN_OWNER_TEST=" + db.parsed.url.render_as_string(hide_password=False) + "\n",
        encoding="utf-8",
    )
    args = [
        "postgres",
        "owner",
        "transfer",
        "--dsn-env",
        "DBTALK_DSN_OWNER_TEST",
        "--database",
        db.database,
        "--from-role",
        "postgres",
        "--to-role",
        db.target,
        "--schema",
        "public",
        "--schema",
        "business",
    ]
    preview = CliRunner().invoke(cli, args)
    assert preview.exit_code == 0, preview.output
    assert "Preview only" in preview.output and "ALTER TABLE ONLY" in preview.output
    with db.engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT relowner FROM pg_catalog.pg_class WHERE oid = 'business.items'::regclass"
            ).scalar_one()
            == 10
        )
    executed = CliRunner().invoke(cli, [*args, "--yes"])
    assert executed.exit_code == 0, executed.output
    assert "ownership transferred" in executed.output
