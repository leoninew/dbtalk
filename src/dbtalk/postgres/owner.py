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
from dbtalk.database.models import (
    DatabaseOperationError,
    driver_error_detail,
    sanitize_error_detail,
)

from .owner_transfer import TransferPlan, transfer_owned
from .role import _validate_identifier, resolve_management_dsn

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@dataclass(frozen=True)
class OwnershipPreview:
    database: str
    objects: tuple[tuple[str, int], ...]
    shared_objects: tuple[tuple[str, int], ...]
    shared_details: tuple[tuple[str, str, str], ...]


@click.group("owner", cls=DbtalkGroup, context_settings=CONTEXT_SETTINGS)
def owner() -> None:
    """Inspect and transfer PostgreSQL object ownership."""


@owner.command("reassign", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option("--database", "database_name", required=True, help="Database to connect to.")
@click.option("--from-role", required=True, help="Current object owner.")
@click.option("--to-role", required=True, help="New object owner.")
@click.option("--yes", is_flag=True, help="Execute native REASSIGN OWNED.")
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
            "PostgreSQL ownership reassignment failed: "
            f"{sanitize_error_detail(driver_error_detail(error))}"
        ) from error
    finally:
        if engine is not None:
            engine.dispose()


def _preview(
    connection: Connection, database_name: str, from_role: str, to_role: str
) -> OwnershipPreview:
    role_rows = connection.execute(
        text("SELECT oid, rolname FROM pg_catalog.pg_roles WHERE rolname IN (:source, :target)"),
        {"source": from_role, "target": to_role},
    ).all()
    roles = {str(row[1]): int(row[0]) for row in role_rows}
    if set(roles) != {from_role, to_role}:
        raise DatabaseOperationError("both PostgreSQL roles must exist")
    # PostgreSQL 18 IsPinnedObject uses FirstUnpinnedObjectId, including renamed roles.
    if roles[from_role] < 12000:
        raise DatabaseOperationError(
            "native REASSIGN OWNED cannot transfer an initialization/system role; "
            "use postgres owner transfer for business objects"
        )

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
    shared_details = connection.execute(
        text(
            "SELECT 'database', datname, pg_catalog.pg_get_userbyid(datdba) "
            "FROM pg_catalog.pg_database WHERE datdba = :source_oid "
            "UNION ALL SELECT 'tablespace', spcname, pg_catalog.pg_get_userbyid(spcowner) "
            "FROM pg_catalog.pg_tablespace WHERE spcowner = :source_oid ORDER BY 1, 2"
        ),
        {"source_oid": roles[from_role]},
    ).all()
    return OwnershipPreview(
        database_name,
        tuple((str(kind), int(count)) for kind, count in objects),
        tuple((str(kind), int(count)) for kind, count in shared),
        tuple((str(kind), str(name), str(owner)) for kind, name, owner in shared_details),
    )


def _render_preview(preview: OwnershipPreview, from_role: str, to_role: str) -> str:
    rows = (
        *(("current database", kind, count) for kind, count in preview.objects),
        *(("shared (cluster-wide)", kind, count) for kind, count in preview.shared_objects),
    )
    rendered = tabulate(rows, headers=("scope", "catalog", "objects"), tablefmt="psql")
    if preview.shared_details:
        rendered += "\n" + tabulate(
            preview.shared_details, headers=("shared kind", "name", "owner"), tablefmt="psql"
        )
    return (
        f"REASSIGN OWNED BY {from_role} TO {to_role} on {preview.database}\n"
        f"{rendered}\n"
        "All objects owned by the source role in this database and shared objects "
        "(including databases and tablespaces) are in scope."
    )


@owner.command("transfer", context_settings=CONTEXT_SETTINGS)
@click.option("--dsn", "dsn_value", help="Complete PostgreSQL SQLAlchemy-style DSN.")
@click.option("--dsn-env", help="Environment variable containing the PostgreSQL DSN.")
@click.option(
    "--database", "database_name", required=True, help="Database containing business objects."
)
@click.option("--from-role", required=True, help="Current business-object owner.")
@click.option("--to-role", required=True, help="New business-object owner.")
@click.option(
    "--schema", "schemas", multiple=True, help="Business schema. Repeat to select several."
)
@click.option(
    "--yes", is_flag=True, help="Execute the business-object ALTER plan in one transaction."
)
def transfer_command(
    dsn_value: str | None,
    dsn_env: str | None,
    database_name: str,
    from_role: str,
    to_role: str,
    schemas: tuple[str, ...],
    yes: bool,
) -> None:
    """Transfer business objects, excluding system and extension objects."""

    try:
        plan = transfer_owned(
            resolve_management_dsn(dsn_value, dsn_env),
            database_name,
            from_role,
            to_role,
            schemas=schemas,
            execute=yes,
        )
    except DatabaseOperationError as error:
        raise click.ClickException(str(error)) from error
    click.echo(_render_transfer(plan))
    if yes:
        click.echo(f"PostgreSQL business ownership transferred ({len(plan.objects)} objects).")
    else:
        click.echo("Preview only; pass --yes to execute this business-object transfer.")


def _render_transfer(plan: TransferPlan) -> str:
    rows = [
        (status, obj.kind, obj.identity, obj.owner, obj.reason)
        for status, objects in (
            ("transfer", plan.objects),
            ("automatic", plan.automatic),
            ("excluded", plan.excluded),
            ("blocked", plan.blocked),
        )
        for obj in objects
    ]
    result = f"Business ownership plan on {plan.database}\n"
    result += tabulate(
        rows, headers=("status", "kind", "object", "owner", "reason"), tablefmt="psql"
    )
    if plan.objects:
        result += "\nPlanned SQL:\n" + "\n".join(f"{obj.statement};" for obj in plan.objects)
    elif not plan.blocked:
        result += "\nNo business objects need transfer."
    if plan.blocked:
        result += "\nExecution is blocked until the listed object conflicts are resolved."
    return result + "\nDatabase and tablespace ownership are outside this command's scope."


__all__ = ["OwnershipPreview", "owner", "reassign_owned"]
