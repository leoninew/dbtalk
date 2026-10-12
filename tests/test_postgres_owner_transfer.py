"""Ownership-plan boundaries independent of a PostgreSQL server."""

from __future__ import annotations

from dataclasses import replace

import pytest
from click.testing import CliRunner

import dbtalk.postgres.owner as owner_cli
import dbtalk.postgres.owner_transfer as transfer
from dbtalk.cli import cli
from dbtalk.database.dsn import parse_dsn
from dbtalk.database.models import DatabaseOperationError
from dbtalk.postgres.owner_transfer import OwnershipObject, TransferPlan


def obj(
    oid: int,
    kind: str = "table",
    *,
    catalog: str = "pg_class",
    schema: str | None = "public",
    owner_oid: int = 10,
    extension: str | None = None,
) -> OwnershipObject:
    return OwnershipObject(
        catalog, oid, kind, schema, f'public."Object {oid}"', owner_oid, "source", extension
    )


def plan(
    objects: list[OwnershipObject],
    edges: dict[transfer.ObjectKey, set[transfer.ObjectKey]] | None = None,
    *,
    unscoped: bool = False,
) -> TransferPlan:
    return transfer.build_transfer_plan(
        "app",
        10,
        20000,
        '"TargetRole"',
        {item.key: item for item in objects},
        edges or {},
        {"public"},
        include_unscoped=unscoped,
    )


def test_plan_uses_real_owner_and_groups_table_automatic_changes() -> None:
    table = obj(1)
    row_type = obj(2, "composite", catalog="pg_type")
    array = obj(3, "base type", catalog="pg_type")
    index = obj(4, "index")
    sequence = obj(5, "sequence")
    toast = obj(6, "toast", schema="pg_toast")
    other_owner = obj(7, owner_oid=20001)
    other_schema = obj(8, schema="other")
    result = plan(
        [table, row_type, array, index, sequence, toast, other_owner, other_schema],
        {table.key: {row_type.key, index.key, sequence.key, toast.key}, row_type.key: {array.key}},
    )

    assert [item.key for item in result.objects] == [table.key]
    assert result.objects[0].statement == 'ALTER TABLE ONLY public."Object 1" OWNER TO "TargetRole"'
    assert {item.key for item in result.automatic} == {
        row_type.key,
        array.key,
        index.key,
        sequence.key,
        toast.key,
    }
    assert not result.blocked


def test_extension_member_and_automatic_objects_are_excluded() -> None:
    member = obj(1, extension="sample")
    index = obj(2, "index")
    business = obj(3)
    result = plan([member, index, business], {member.key: {index.key}})

    assert [item.key for item in result.objects] == [business.key]
    assert {item.key for item in result.excluded} == {member.key, index.key}
    assert not result.blocked


def test_automatic_owner_change_to_extension_member_blocks_the_plan() -> None:
    table = obj(1)
    sequence = obj(
        2,
        "sequence",
        extension="sample",
    )
    result = plan([table, sequence], {table.key: {sequence.key}})

    assert [item.key for item in result.blocked] == [sequence.key]


def test_unscoped_unsupported_objects_block_only_database_scope() -> None:
    table = obj(1)
    publication = obj(2, "publication", catalog="pg_publication", schema=None)
    assert not plan([table, publication]).blocked
    assert [item.kind for item in plan([table, publication], unscoped=True).blocked] == [
        "publication"
    ]
    operator = obj(3, "operator", catalog="pg_operator")
    assert plan([table, operator]).blocked[0].kind == "operator"


def test_routine_identity_and_type_sql_are_preserved() -> None:
    function = replace(obj(1, "function", catalog="pg_proc"), identity='public."Overload"(integer)')
    domain = obj(2, "domain", catalog="pg_type")
    result = plan([function, domain])

    assert [item.kind for item in result.objects] == ["domain", "function"]
    assert (
        result.objects[1].statement
        == 'ALTER FUNCTION public."Overload"(integer) OWNER TO "TargetRole"'
    )
    assert result.objects[0].statement == 'ALTER DOMAIN public."Object 2" OWNER TO "TargetRole"'


def test_transfer_cli_previews_and_passes_explicit_schema_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], bool]] = []
    monkeypatch.setattr(
        owner_cli,
        "resolve_management_dsn",
        lambda *_: parse_dsn("postgresql+psycopg://admin@db/app"),
    )

    def transfer_owned(*_: object, schemas: tuple[str, ...], execute: bool) -> TransferPlan:
        calls.append((schemas, execute))
        return plan([obj(1)])

    monkeypatch.setattr(owner_cli, "transfer_owned", transfer_owned)
    args = [
        "postgres",
        "owner",
        "transfer",
        "--dsn-env",
        "DBTALK_DSN_ADMIN",
        "--database",
        "app",
        "--from-role",
        "postgres",
        "--to-role",
        "target",
        "--schema",
        "public",
        "--schema",
        "business",
    ]
    preview = CliRunner().invoke(cli, args)
    executed = CliRunner().invoke(cli, [*args, "--yes"])

    assert preview.exit_code == executed.exit_code == 0
    assert calls == [(("public", "business"), False), (("public", "business"), True)]
    assert "Planned SQL" in preview.output
    assert "Preview only" in preview.output
    assert "ownership transferred" in executed.output
    assert "outside this command's scope" in preview.output


def test_transfer_help_has_no_shared_object_option() -> None:
    result = CliRunner().invoke(cli, ["postgres", "owner", "transfer", "--help"])
    assert result.exit_code == 0
    assert "--schema" in result.output
    assert "--include-shared" not in result.output


def test_execution_refuses_blocked_plan_before_any_sql() -> None:
    class NoSqlConnection:
        def exec_driver_sql(self, _: str) -> None:
            pytest.fail("blocked plans must not execute SQL")

    result = plan([obj(1, "operator", catalog="pg_operator")])
    with pytest.raises(DatabaseOperationError, match="ownership transfer blocked"):
        transfer._execute_plan(NoSqlConnection(), result, {}, {})  # type: ignore[arg-type]
