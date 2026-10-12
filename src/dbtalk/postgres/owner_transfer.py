"""Plan and transfer PostgreSQL business-object ownership in one database."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine, create_engine
from sqlalchemy.exc import SQLAlchemyError

from dbtalk.database.dsn import ParsedDsn
from dbtalk.database.models import (
    DatabaseOperationError,
    driver_error_detail,
    sanitize_error_detail,
)

from .role import _validate_identifier

ObjectKey = tuple[str, int]


@dataclass(frozen=True)
class OwnershipObject:
    catalog: str
    oid: int
    kind: str
    schema: str | None
    identity: str
    owner_oid: int
    owner: str
    extension: str | None = None
    statement: str | None = None
    reason: str = ""

    @property
    def key(self) -> ObjectKey:
        return self.catalog, self.oid


@dataclass(frozen=True)
class TransferPlan:
    database: str
    source_oid: int
    target_oid: int
    objects: tuple[OwnershipObject, ...]
    automatic: tuple[OwnershipObject, ...]
    excluded: tuple[OwnershipObject, ...]
    blocked: tuple[OwnershipObject, ...]


@dataclass(frozen=True)
class _Catalog:
    name: str
    owner: str
    kind: str
    namespace: str = "NULL::oid"
    condition: str = "obj.oid >= 16384"


_CATALOGS = (
    _Catalog("pg_namespace", "nspowner", "'schema'", "obj.oid", "TRUE"),
    _Catalog(
        "pg_class",
        "relowner",
        "CASE obj.relkind WHEN 'r' THEN 'table' WHEN 'p' THEN 'partitioned table' "
        "WHEN 'f' THEN 'foreign table' WHEN 'v' THEN 'view' "
        "WHEN 'm' THEN 'materialized view' WHEN 'S' THEN 'sequence' "
        "WHEN 'i' THEN 'index' WHEN 'I' THEN 'partitioned index' "
        "WHEN 't' THEN 'toast' WHEN 'c' THEN 'composite storage' ELSE 'relation' END",
        "obj.relnamespace",
    ),
    _Catalog(
        "pg_proc",
        "proowner",
        "CASE obj.prokind WHEN 'p' THEN 'procedure' WHEN 'a' THEN 'aggregate' ELSE 'function' END",
        "obj.pronamespace",
    ),
    _Catalog(
        "pg_type",
        "typowner",
        "CASE obj.typtype WHEN 'e' THEN 'enum' WHEN 'd' THEN 'domain' "
        "WHEN 'c' THEN 'composite' WHEN 'r' THEN 'range' "
        "WHEN 'm' THEN 'multirange' ELSE 'base type' END",
        "obj.typnamespace",
    ),
    _Catalog("pg_operator", "oprowner", "'operator'", "obj.oprnamespace"),
    _Catalog("pg_opclass", "opcowner", "'operator class'", "obj.opcnamespace"),
    _Catalog("pg_opfamily", "opfowner", "'operator family'", "obj.opfnamespace"),
    _Catalog("pg_collation", "collowner", "'collation'", "obj.collnamespace"),
    _Catalog("pg_conversion", "conowner", "'conversion'", "obj.connamespace"),
    _Catalog("pg_ts_config", "cfgowner", "'text search configuration'", "obj.cfgnamespace"),
    _Catalog("pg_ts_dict", "dictowner", "'text search dictionary'", "obj.dictnamespace"),
    _Catalog("pg_statistic_ext", "stxowner", "'extended statistics'", "obj.stxnamespace"),
    _Catalog("pg_extension", "extowner", "'extension'", "obj.extnamespace"),
    _Catalog("pg_publication", "pubowner", "'publication'"),
    _Catalog("pg_subscription", "subowner", "'subscription'"),
    _Catalog("pg_foreign_data_wrapper", "fdwowner", "'foreign data wrapper'"),
    _Catalog("pg_foreign_server", "srvowner", "'foreign server'"),
    _Catalog("pg_language", "lanowner", "'language'"),
    _Catalog("pg_event_trigger", "evtowner", "'event trigger'"),
    _Catalog("pg_largeobject_metadata", "lomowner", "'large object'", condition="TRUE"),
)
_CATALOG_BY_NAME = {catalog.name: catalog for catalog in _CATALOGS}
_ALTER_KINDS = {
    "schema": "SCHEMA",
    "table": "TABLE ONLY",
    "partitioned table": "TABLE ONLY",
    "foreign table": "FOREIGN TABLE",
    "view": "VIEW",
    "materialized view": "MATERIALIZED VIEW",
    "sequence": "SEQUENCE",
    "function": "FUNCTION",
    "procedure": "PROCEDURE",
    "aggregate": "AGGREGATE",
    "enum": "TYPE",
    "domain": "DOMAIN",
    "composite": "TYPE",
    "range": "TYPE",
}

# These are the owner-propagation edges used by ATExecChangeOwner and
# AlterTypeOwnerInternal, rather than general object dependencies.
_OWNER_EDGES_SQL = """
SELECT 'pg_class', indrelid, 'pg_class', indexrelid FROM pg_catalog.pg_index
UNION
SELECT 'pg_class', oid, 'pg_class', reltoastrelid FROM pg_catalog.pg_class
WHERE reltoastrelid <> 0
UNION
SELECT 'pg_class', oid, 'pg_type', reltype FROM pg_catalog.pg_class
WHERE reltype <> 0 AND relkind <> 'c'
UNION
SELECT 'pg_type', oid, 'pg_class', typrelid FROM pg_catalog.pg_type
WHERE typrelid <> 0 AND typtype = 'c'
  AND EXISTS (SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid = typrelid AND c.relkind = 'c')
UNION
SELECT 'pg_type', oid, 'pg_type', typarray FROM pg_catalog.pg_type WHERE typarray <> 0
UNION
SELECT 'pg_type', rngtypid, 'pg_type', rngmultitypid FROM pg_catalog.pg_range
UNION
SELECT 'pg_class', d.refobjid, 'pg_class', d.objid FROM pg_catalog.pg_depend d
JOIN pg_catalog.pg_class c ON c.oid = d.objid AND c.relkind = 'S'
WHERE d.classid = 'pg_catalog.pg_class'::regclass
  AND d.refclassid = 'pg_catalog.pg_class'::regclass
  AND d.refobjsubid > 0 AND d.deptype IN ('a', 'i')
"""


def _catalog_sql(catalog: _Catalog, *, by_oid: bool = False) -> str:
    condition = "obj.oid = :oid" if by_oid else catalog.condition
    return (
        f"SELECT '{catalog.name}' AS catalog, obj.oid, {catalog.kind} AS kind, "
        f"n.nspname AS schema_name, "
        f"(pg_catalog.pg_identify_object('pg_catalog.{catalog.name}'::regclass, "
        f"obj.oid, 0)).identity AS identity, obj.{catalog.owner} AS owner_oid, "
        f"pg_catalog.pg_get_userbyid(obj.{catalog.owner}) AS owner, "
        "e.extname AS extension "
        f"FROM pg_catalog.{catalog.name} obj "
        f"LEFT JOIN pg_catalog.pg_namespace n ON n.oid = {catalog.namespace} "
        "LEFT JOIN pg_catalog.pg_depend d "
        f"ON d.classid = 'pg_catalog.{catalog.name}'::regclass "
        "AND d.objid = obj.oid AND d.objsubid = 0 AND d.deptype = 'e' "
        "LEFT JOIN pg_catalog.pg_extension e ON e.oid = d.refobjid "
        f"WHERE {condition}"
    )


_INVENTORY_SQL = " UNION ALL ".join(_catalog_sql(catalog) for catalog in _CATALOGS)


def _object(row: Any) -> OwnershipObject:
    return OwnershipObject(
        str(row[0]),
        int(row[1]),
        str(row[2]),
        None if row[3] is None else str(row[3]),
        str(row[4]),
        int(row[5]),
        str(row[6]),
        None if row[7] is None else str(row[7]),
    )


def _inventory(connection: Connection) -> dict[ObjectKey, OwnershipObject]:
    objects = (_object(row) for row in connection.execute(text(_INVENTORY_SQL)))
    return {obj.key: obj for obj in objects}


def _edges(connection: Connection) -> dict[ObjectKey, set[ObjectKey]]:
    edges: dict[ObjectKey, set[ObjectKey]] = {}
    for row in connection.execute(text(_OWNER_EDGES_SQL)):
        edges.setdefault((str(row[0]), int(row[1])), set()).add((str(row[2]), int(row[3])))
    return edges


def _descendants(key: ObjectKey, edges: dict[ObjectKey, set[ObjectKey]]) -> set[ObjectKey]:
    seen: set[ObjectKey] = set()
    pending = list(edges.get(key, ()))
    while pending:
        child = pending.pop()
        if child not in seen:
            seen.add(child)
            pending.extend(edges.get(child, ()))
    return seen


def _system_schema(name: str) -> bool:
    return name.startswith("pg_") or name == "information_schema"


def _selected_schemas(connection: Connection, schemas: tuple[str, ...]) -> set[str]:
    names = set(connection.exec_driver_sql("SELECT nspname FROM pg_catalog.pg_namespace").scalars())
    for name in schemas:
        _validate_identifier(name, "PostgreSQL schema name")
        if _system_schema(name):
            raise DatabaseOperationError(f"system schema cannot be transferred: {name}")
        if name not in names:
            raise DatabaseOperationError(f"PostgreSQL schema does not exist: {name}")
    return (
        set(schemas) if schemas else {str(name) for name in names if not _system_schema(str(name))}
    )


def _sort_object(obj: OwnershipObject) -> tuple[int, str, int]:
    priority = 0 if obj.kind == "schema" else 1 if obj.catalog == "pg_type" else 2
    return priority, obj.identity, obj.oid


def build_transfer_plan(
    database: str,
    source_oid: int,
    target_oid: int,
    target_sql: str,
    inventory: dict[ObjectKey, OwnershipObject],
    edges: dict[ObjectKey, set[ObjectKey]],
    schemas: set[str],
    *,
    include_unscoped: bool,
) -> TransferPlan:
    """Classify independent objects and their actual automatic owner changes."""

    protected: set[ObjectKey] = set()
    for obj in inventory.values():
        if obj.extension or obj.kind == "extension":
            protected.add(obj.key)
            protected.update(_descendants(obj.key, edges))
    selected = {
        key: obj
        for key, obj in inventory.items()
        if obj.owner_oid == source_oid
        and (obj.schema in schemas or (obj.schema is None and include_unscoped))
    }
    excluded = {
        key: replace(obj, reason="extension or an automatic object of an extension member")
        for key, obj in selected.items()
        if key in protected
    }
    candidates = {key: obj for key, obj in selected.items() if key not in protected}
    parents: dict[ObjectKey, ObjectKey] = {
        child: parent for parent, children in edges.items() for child in children
    }
    roots = {key: obj for key, obj in candidates.items() if key not in parents}
    blocked: dict[ObjectKey, OwnershipObject] = {}
    automatic: dict[ObjectKey, OwnershipObject] = {}
    changes: list[OwnershipObject] = []
    for key, obj in sorted(roots.items(), key=lambda item: _sort_object(item[1])):
        if obj.kind not in _ALTER_KINDS:
            blocked[key] = replace(obj, reason="object kind is not supported by owner transfer")
            continue
        changes.append(
            replace(
                obj,
                statement=f"ALTER {_ALTER_KINDS[obj.kind]} {obj.identity} OWNER TO {target_sql}",
            )
        )
        for child_key in _descendants(key, edges):
            child = inventory.get(child_key)
            if child is None:
                blocked[key] = replace(
                    obj, reason="automatic owner object is missing from inventory"
                )
                continue
            reason = ""
            if child_key in protected:
                reason = f"{obj.identity} would change an extension member"
            elif child.owner_oid != source_oid:
                reason = f"{obj.identity} would change an object owned by another role"
            elif child.schema not in schemas and child.kind not in (
                "toast",
                "index",
                "partitioned index",
                "composite",
                "base type",
            ):
                reason = f"{obj.identity} would change an object outside the selected schemas"
            if reason:
                blocked[child_key] = replace(child, reason=reason)
            automatic[child_key] = replace(child, reason=f"follows {obj.identity}")
    for key, obj in candidates.items():
        if key not in roots and key not in automatic:
            blocked[key] = replace(
                obj,
                reason="automatic owner object has no transferable parent in the selected scope",
            )
    return TransferPlan(
        database,
        source_oid,
        target_oid,
        tuple(changes),
        tuple(sorted(automatic.values(), key=_sort_object)),
        tuple(sorted(excluded.values(), key=_sort_object)),
        tuple(sorted(blocked.values(), key=_sort_object)),
    )


def _roles(connection: Connection, from_role: str, to_role: str) -> tuple[int, int]:
    roles = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            text(
                "SELECT rolname, oid FROM pg_catalog.pg_roles WHERE rolname IN (:source, :target)"
            ),
            {"source": from_role, "target": to_role},
        )
    }
    if set(roles) != {from_role, to_role}:
        raise DatabaseOperationError("both PostgreSQL roles must exist")
    return int(roles[from_role]), int(roles[to_role])


def _check_permissions(connection: Connection, plan: TransferPlan) -> None:
    row = connection.execute(
        text(
            "SELECT rolsuper, pg_catalog.pg_has_role(current_user, CAST(:source AS oid), 'USAGE'), "
            "pg_catalog.pg_has_role(current_user, CAST(:target AS oid), 'SET') "
            "FROM pg_catalog.pg_roles WHERE rolname = current_user"
        ),
        {"source": plan.source_oid, "target": plan.target_oid},
    ).one()
    if row[0]:
        return
    if not row[1] or not row[2]:
        raise DatabaseOperationError(
            "executor must inherit source ownership and be able to SET target role"
        )
    transferring_schemas = {obj.schema for obj in plan.objects if obj.kind == "schema"}
    if (
        transferring_schemas
        and not connection.execute(
            text(
                "SELECT pg_catalog.has_database_privilege("
                "CAST(:target AS oid), current_database(), 'CREATE')"
            ),
            {"target": plan.target_oid},
        ).scalar_one()
    ):
        raise DatabaseOperationError("target role requires CREATE on the database to own a schema")
    for schema in {obj.schema for obj in plan.objects if obj.schema} - transferring_schemas:
        if not connection.execute(
            text("SELECT pg_catalog.has_schema_privilege(CAST(:target AS oid), :schema, 'CREATE')"),
            {"target": plan.target_oid, "schema": schema},
        ).scalar_one():
            raise DatabaseOperationError(f"target role requires CREATE on schema {schema}")


def _read_object(connection: Connection, obj: OwnershipObject) -> OwnershipObject | None:
    row = connection.execute(
        text(_catalog_sql(_CATALOG_BY_NAME[obj.catalog], by_oid=True)), {"oid": obj.oid}
    ).first()
    return None if row is None else _object(row)


def _same_identity(
    actual: OwnershipObject | None, expected: OwnershipObject, owner_oid: int
) -> bool:
    return (
        actual is not None
        and actual.identity == expected.identity
        and actual.kind == expected.kind
        and actual.schema == expected.schema
        and actual.owner_oid == owner_oid
        and actual.extension == expected.extension
    )


def _execute_plan(
    connection: Connection,
    plan: TransferPlan,
    inventory: dict[ObjectKey, OwnershipObject],
    edges: dict[ObjectKey, set[ObjectKey]],
) -> None:
    if plan.blocked:
        first = plan.blocked[0]
        raise DatabaseOperationError(
            f"ownership transfer blocked: {first.identity}: {first.reason}"
        )
    if not plan.objects:
        return
    _check_permissions(connection, plan)
    for obj in plan.objects:
        if obj.kind in ("table", "partitioned table"):
            connection.exec_driver_sql(f"LOCK TABLE ONLY {obj.identity} IN ACCESS EXCLUSIVE MODE")
    for obj in (*plan.objects, *plan.automatic, *plan.excluded):
        if not _same_identity(_read_object(connection, obj), obj, obj.owner_oid):
            raise DatabaseOperationError(f"object changed since ownership planning: {obj.identity}")
    current_edges = _edges(connection)
    for obj in plan.objects:
        if _descendants(obj.key, current_edges) != _descendants(obj.key, edges):
            raise DatabaseOperationError(f"automatic owner dependencies changed: {obj.identity}")
    protected = {
        key: obj for key, obj in inventory.items() if obj.extension or obj.kind == "extension"
    }
    for obj in plan.objects:
        if not _same_identity(_read_object(connection, obj), obj, obj.owner_oid):
            raise DatabaseOperationError(f"object changed before owner ALTER: {obj.identity}")
        try:
            assert obj.statement is not None
            connection.exec_driver_sql(obj.statement)
        except SQLAlchemyError as error:
            raise DatabaseOperationError(
                f"ownership transfer failed on {obj.identity}: "
                f"{sanitize_error_detail(driver_error_detail(error))}"
            ) from error
    for obj in (*plan.objects, *plan.automatic):
        if not _same_identity(_read_object(connection, obj), obj, plan.target_oid):
            raise DatabaseOperationError(f"ownership result does not match plan: {obj.identity}")
    for obj in protected.values():
        if not _same_identity(_read_object(connection, obj), obj, obj.owner_oid):
            raise DatabaseOperationError(f"excluded extension object changed: {obj.identity}")
    final_edges = _edges(connection)
    for obj in plan.objects:
        if _descendants(obj.key, final_edges) != _descendants(obj.key, edges):
            raise DatabaseOperationError(f"automatic owner dependencies changed: {obj.identity}")


def transfer_owned(
    parsed: ParsedDsn,
    database_name: str,
    from_role: str,
    to_role: str,
    *,
    schemas: tuple[str, ...] = (),
    execute: bool = False,
) -> TransferPlan:
    """Preview or execute an explicitly scoped business-object ownership transfer."""

    if parsed.async_mode or parsed.dialect != "postgresql":
        raise DatabaseOperationError("ownership transfer requires a synchronous PostgreSQL DSN")
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
            source_oid, target_oid = _roles(connection, from_role, to_role)
            selected = _selected_schemas(connection, schemas)
            inventory = _inventory(connection)
            edges = _edges(connection)
            quoted_target = connection.dialect.identifier_preparer.quote(to_role)
            plan = build_transfer_plan(
                current,
                source_oid,
                target_oid,
                quoted_target,
                inventory,
                edges,
                selected,
                include_unscoped=not schemas,
            )
            if execute:
                _execute_plan(connection, plan, inventory, edges)
            return plan
    except DatabaseOperationError:
        raise
    except SQLAlchemyError as error:
        raise DatabaseOperationError(
            "PostgreSQL ownership transfer failed: "
            f"{sanitize_error_detail(driver_error_detail(error))}"
        ) from error
    finally:
        if engine is not None:
            engine.dispose()


__all__ = ["OwnershipObject", "TransferPlan", "transfer_owned"]
