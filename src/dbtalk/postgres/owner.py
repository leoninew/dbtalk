"""Explicit PostgreSQL object ownership reassignment."""

from __future__ import annotations

from dataclasses import dataclass

import click
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError
from tabulate import tabulate

from dbtalk.cli_runtime import DbtalkGroup
from dbtalk.database.dsn import ParsedDsn
from dbtalk.database.models import DatabaseOperationError, sanitize_error_detail

from .role import _validate_identifier, resolve_management_dsn

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@dataclass(frozen=True)
class OwnershipPreview:
    database: str
    objects: tuple[tuple[str, int], ...]
    shared_objects: tuple[tuple[str, int], ...]


@click.group("owner", cls=DbtalkGroup, context_settings=CONTEXT_SETTINGS)
def owner() -> None:
    """Inspect and reassign PostgreSQL object ownership."""


@owner.command("reassign", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option("--database", "database_name", required=True, help="Database to connect to.")
@click.option("--from-role", required=True, help="Current object owner.")
@click.option("--to-role", required=True, help="New object owner.")
@click.option("--yes", is_flag=True, help="Execute REASSIGN OWNED after preview.")
@click.option(
    "--include-shared",
    is_flag=True,
    help="Explicitly allow changing ownership of shared databases and tablespaces.",
)
def reassign_command(
    dsn_value: str | None,
    dsn_env: str | None,
    database_name: str,
    from_role: str,
    to_role: str,
    yes: bool,
    include_shared: bool,
) -> None:
    """Preview or transfer all objects owned by a role in one database."""

    try:
        parsed = resolve_management_dsn(dsn_value, dsn_env)
        preview = reassign_owned(
            parsed,
            database_name,
            from_role,
            to_role,
            execute=yes,
            include_shared=include_shared,
        )
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(_render_preview(preview, from_role, to_role))
    if yes:
        click.echo("PostgreSQL ownership reassigned.")
    else:
        click.echo("Preview only; pass --yes to reassign ownership.")


def reassign_owned(
    parsed: ParsedDsn,
    database_name: str,
    from_role: str,
    to_role: str,
    *,
    execute: bool = False,
    include_shared: bool = False,
) -> OwnershipPreview:
    """Inspect owned objects and optionally run PostgreSQL's native reassignment."""

    if parsed.async_mode or parsed.dialect != "postgresql":
        raise DatabaseOperationError("ownership reassignment requires a synchronous PostgreSQL DSN")
    _validate_identifier(database_name, "PostgreSQL database name")
    _validate_identifier(from_role, "PostgreSQL source role name")
    _validate_identifier(to_role, "PostgreSQL target role name")
    if from_role == to_role:
        raise DatabaseOperationError("source and target PostgreSQL roles must differ")

    engine: Engine | None = None
    try:
        engine = create_engine(parsed.url.set(database=database_name))
        with engine.connect() as connection, connection.begin():
            current = str(connection.exec_driver_sql("SELECT current_database()").scalar_one())
            if current != database_name:
                raise DatabaseOperationError(
                    "connected PostgreSQL database does not match --database"
                )
            preview = _preview(connection, current, from_role, to_role)
            if execute and preview.shared_objects and not include_shared:
                raise DatabaseOperationError(
                    "source role owns shared objects; --include-shared is required"
                )
            if execute:
                quoted_from = connection.dialect.identifier_preparer.quote(from_role)
                quoted_to = connection.dialect.identifier_preparer.quote(to_role)
                connection.exec_driver_sql(f"REASSIGN OWNED BY {quoted_from} TO {quoted_to}")
            return preview
    except DatabaseOperationError:
        raise
    except SQLAlchemyError as error:
        raise DatabaseOperationError(
            f"PostgreSQL ownership reassignment failed: {sanitize_error_detail(str(error))}"
        ) from error
    finally:
        if engine is not None:
            engine.dispose()


def _preview(
    connection: Connection, database_name: str, from_role: str, to_role: str
) -> OwnershipPreview:
    roles = (
        connection.execute(
            text("SELECT rolname FROM pg_catalog.pg_roles WHERE rolname IN (:source, :target)"),
            {"source": from_role, "target": to_role},
        )
        .scalars()
        .all()
    )
    if set(roles) != {from_role, to_role}:
        raise DatabaseOperationError("both PostgreSQL roles must exist")

    objects = connection.execute(
        text(
            "SELECT classid::regclass::text, count(*) FROM pg_catalog.pg_shdepend "
            "WHERE refobjid = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = :source) "
            "AND deptype = 'o' AND dbid = (SELECT oid FROM pg_catalog.pg_database "
            "WHERE datname = current_database()) GROUP BY classid ORDER BY classid::regclass::text"
        ),
        {"source": from_role},
    ).all()
    shared = connection.execute(
        text(
            "SELECT classid::regclass::text, count(*) FROM pg_catalog.pg_shdepend "
            "WHERE refobjid = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = :source) "
            "AND deptype = 'o' AND dbid = 0 GROUP BY classid "
            "ORDER BY classid::regclass::text"
        ),
        {"source": from_role},
    ).all()
    return OwnershipPreview(
        database_name,
        tuple((str(kind), int(count)) for kind, count in objects),
        tuple((str(kind), int(count)) for kind, count in shared),
    )


def _render_preview(preview: OwnershipPreview, from_role: str, to_role: str) -> str:
    rows = (
        *(("current database", kind, count) for kind, count in preview.objects),
        *(("shared (cluster-wide)", kind, count) for kind, count in preview.shared_objects),
    )
    rendered = tabulate(rows, headers=("scope", "catalog", "objects"), tablefmt="psql")
    return (
        f"REASSIGN OWNED BY {from_role} TO {to_role} on {preview.database}\n"
        f"{rendered}\n"
        "All objects owned by the source role in this database and shared objects "
        "(including databases and tablespaces) are in scope."
    )


__all__ = ["OwnershipPreview", "owner", "reassign_owned"]
