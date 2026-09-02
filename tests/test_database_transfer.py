from __future__ import annotations

import gzip
import io
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from click.testing import CliRunner

from dbtalk.cli import cli as main
from dbtalk.database.dsn import sqlite_dsn
from dbtalk.database.mysql import (
    _encode_mysql_value,
    _mysql_time_of_day,
)
from dbtalk.database.transfer import (
    ColumnDefinition,
    DatabaseTransferError,
    ExportOptions,
    ImportOptions,
    JSONValue,
    TableBlock,
    TableBlockHeader,
    TransferConnection,
    TransferHeader,
    TransferSummary,
    compatible_types,
    decode_value,
    encode_value,
    export_database,
    import_database,
    read_jsonl,
    write_jsonl,
)


class DatabaseTransferTests(unittest.TestCase):
    def test_cli_exports_and_imports_gzip_jsonl(self) -> None:
        runner = CliRunner()

        with runner.isolated_filesystem():
            source = Path("source.db")
            target = Path("target.db")
            self._create_schema(source)
            self._create_schema(target)
            source_connection = sqlite3.connect(source)
            source_connection.execute("INSERT INTO parent(id, name) VALUES (1, 'Ada')")
            source_connection.commit()
            source_connection.close()

            exported = runner.invoke(
                main,
                [
                    "export",
                    "--source",
                    "sqlite",
                    "--dsn",
                    f"sqlite:///{source.as_posix()}",
                    "--output",
                    "transfer.jsonl",
                    "--archive",
                ],
            )
            compressed_output = Path("transfer.jsonl.gz")
            imported = runner.invoke(
                main,
                [
                    "import",
                    "--target",
                    "sqlite",
                    "--dsn",
                    f"sqlite:///{target.as_posix()}",
                    "--input",
                    str(compressed_output),
                    "--mode",
                    "insert",
                ],
            )

            self.assertEqual(exported.exit_code, 0, exported.output)
            self.assertTrue(compressed_output.is_file())
            with gzip.open(compressed_output, "rt", encoding="utf-8") as compressed:
                self.assertIn('"kind":"header"', compressed.readline())
            self.assertEqual(imported.exit_code, 0, imported.output)
            target_connection = sqlite3.connect(target)
            self.assertEqual(
                target_connection.execute("SELECT name FROM parent WHERE id = 1").fetchone(),
                ("Ada",),
            )
            target_connection.close()

    def test_sqlite_export_and_upsert_preserves_portable_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            target = root / "target.db"
            transfer = root / "nested" / "transfer.jsonl"
            self._create_schema(source)
            self._create_schema(target)
            source_connection = sqlite3.connect(source)
            source_connection.execute(
                "INSERT INTO parent VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    1,
                    "source",
                    "123456789.123456789",
                    "2026-08-19 15:30:00",
                    "2026-08-19",
                    "08:09:10",
                    b"payload",
                ),
            )
            source_connection.execute("INSERT INTO child VALUES (10, 1, 'child')")
            source_connection.commit()
            source_connection.close()
            target_connection = sqlite3.connect(target)
            target_connection.execute("INSERT INTO parent(id, name) VALUES (1, 'old target value')")
            target_connection.commit()
            target_connection.close()

            exported = export_database(
                ExportOptions(
                    TransferConnection("sqlite", dsn=sqlite_dsn(source)),
                    transfer,
                    ZoneInfo("Asia/Shanghai"),
                )
            )
            imported = import_database(
                ImportOptions(
                    TransferConnection("sqlite", dsn=sqlite_dsn(target)),
                    transfer,
                    "upsert",
                    ZoneInfo("UTC"),
                )
            )

            self.assertEqual(exported.table_count, 2)
            self.assertEqual(imported.row_count, 2)
            records = [json.loads(line) for line in transfer.read_text().splitlines()]
            self.assertEqual(records[0]["source"], "sqlite")
            self.assertEqual(records[2]["values"][3], "2026-08-19T07:30:00Z")
            self.assertEqual(
                records[2]["values"][2],
                {"$type": "decimal", "value": "123456789.12345679"},
            )
            self.assertEqual(records[2]["values"][6], {"$type": "blob", "base64": "cGF5bG9hZA=="})
            target_connection = sqlite3.connect(target)
            self.assertEqual(
                target_connection.execute(
                    "SELECT name, amount, happened, day, at, payload FROM parent"
                ).fetchone(),
                (
                    "source",
                    123456789.12345679,
                    "2026-08-19 07:30:00",
                    "2026-08-19",
                    "08:09:10",
                    b"payload",
                ),
            )
            self.assertEqual(
                target_connection.execute("SELECT parent_id, note FROM child").fetchone(),
                (1, "child"),
            )
            target_connection.close()

    def test_insert_rejects_primary_key_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            target = root / "target.db"
            transfer = root / "transfer.jsonl"
            self._create_schema(source)
            self._create_schema(target)
            for path, name in ((source, "source"), (target, "target")):
                connection = sqlite3.connect(path)
                connection.execute("INSERT INTO parent(id, name) VALUES (1, ?)", (name,))
                connection.commit()
                connection.close()
            export_database(
                ExportOptions(
                    TransferConnection("sqlite", dsn=sqlite_dsn(source)),
                    transfer,
                    ZoneInfo("UTC"),
                )
            )

            with self.assertRaisesRegex(DatabaseTransferError, "parent"):
                import_database(
                    ImportOptions(
                        TransferConnection("sqlite", dsn=sqlite_dsn(target)),
                        transfer,
                        "insert",
                        ZoneInfo("UTC"),
                    )
                )

            connection = sqlite3.connect(target)
            self.assertEqual(
                connection.execute("SELECT name FROM parent WHERE id = 1").fetchone(),
                ("target",),
            )
            connection.close()

    def test_upsert_rejects_table_without_primary_key_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.db"
            transfer = root / "transfer.jsonl"
            connection = sqlite3.connect(target)
            connection.execute("CREATE TABLE logs(message TEXT)")
            connection.commit()
            connection.close()
            self._write_transfer(
                transfer,
                "logs",
                (ColumnDefinition("message", "TEXT"),),
                (),
                (("unwritten",),),
            )

            with self.assertRaisesRegex(DatabaseTransferError, "no primary key"):
                import_database(
                    ImportOptions(
                        TransferConnection("sqlite", dsn=sqlite_dsn(target)),
                        transfer,
                        "upsert",
                        ZoneInfo("UTC"),
                    )
                )
            connection = sqlite3.connect(target)
            self.assertEqual(connection.execute("SELECT * FROM logs").fetchall(), [])
            connection.close()

    def test_insert_allows_table_without_primary_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.db"
            transfer = root / "transfer.jsonl"
            connection = sqlite3.connect(target)
            connection.execute("CREATE TABLE logs(message TEXT)")
            connection.commit()
            connection.close()
            self._write_transfer(
                transfer,
                "logs",
                (ColumnDefinition("message", "TEXT"),),
                (),
                (("inserted",),),
            )

            import_database(
                ImportOptions(
                    TransferConnection("sqlite", dsn=sqlite_dsn(target)),
                    transfer,
                    "insert",
                    ZoneInfo("UTC"),
                )
            )

            connection = sqlite3.connect(target)
            self.assertEqual(connection.execute("SELECT * FROM logs").fetchall(), [("inserted",)])
            connection.close()

    def test_import_excludes_selected_tables_before_preflight_and_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            target = root / "target.db"
            transfer = root / "transfer.jsonl"
            self._create_schema(source)
            self._create_schema(target)
            source_connection = sqlite3.connect(source)
            source_connection.execute("INSERT INTO parent(id, name) VALUES (1, 'parent')")
            source_connection.execute("INSERT INTO child VALUES (10, 1, 'child')")
            source_connection.commit()
            source_connection.close()
            export_database(
                ExportOptions(
                    TransferConnection("sqlite", dsn=sqlite_dsn(source)),
                    transfer,
                    ZoneInfo("UTC"),
                )
            )

            imported = import_database(
                ImportOptions(
                    TransferConnection("sqlite", dsn=sqlite_dsn(target)),
                    transfer,
                    "insert",
                    ZoneInfo("UTC"),
                    ("child",),
                )
            )

            self.assertEqual(imported.table_count, 1)
            self.assertEqual(imported.row_count, 1)
            target_connection = sqlite3.connect(target)
            self.assertEqual(
                target_connection.execute("SELECT id, name FROM parent").fetchall(),
                [(1, "parent")],
            )
            self.assertEqual(target_connection.execute("SELECT * FROM child").fetchall(), [])
            target_connection.close()

    def test_export_excludes_selected_tables_before_reading_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            transfer = root / "transfer.jsonl"
            self._create_schema(source)
            connection = sqlite3.connect(source)
            connection.execute("INSERT INTO parent(id, name) VALUES (1, 'parent')")
            connection.execute("INSERT INTO child VALUES (10, 1, 'child')")
            connection.commit()
            connection.close()

            exported = export_database(
                ExportOptions(
                    TransferConnection("sqlite", dsn=sqlite_dsn(source)),
                    transfer,
                    ZoneInfo("UTC"),
                    ("child",),
                )
            )

            records = [json.loads(line) for line in transfer.read_text().splitlines()]
            table_records = [record for record in records if record["kind"] == "table"]
            self.assertEqual(exported.table_count, 1)
            self.assertEqual(exported.row_count, 1)
            self.assertEqual([record["name"] for record in table_records], ["parent"])

    def test_include_tables_are_filtered_before_exclude_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            transfer = root / "transfer.jsonl"
            self._create_schema(source)
            connection = sqlite3.connect(source)
            connection.execute("INSERT INTO parent(id, name) VALUES (1, 'parent')")
            connection.execute("INSERT INTO child VALUES (10, 1, 'child')")
            connection.commit()
            connection.close()

            summary = export_database(
                ExportOptions(
                    TransferConnection("sqlite", dsn=sqlite_dsn(source)),
                    transfer,
                    ZoneInfo("UTC"),
                    include_tables=("parent", "child"),
                    exclude_tables=("child",),
                )
            )

            records = [json.loads(line) for line in transfer.read_text().splitlines()]
            table_records = [record for record in records if record["kind"] == "table"]
            self.assertEqual(summary.table_count, 1)
            self.assertEqual([record["name"] for record in table_records], ["parent"])

    def test_export_blocks_selected_child_without_parent_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            transfer = root / "transfer.jsonl"
            self._create_schema(source)

            with self.assertRaisesRegex(DatabaseTransferError, "unselected"):
                export_database(
                    ExportOptions(
                        TransferConnection("sqlite", dsn=sqlite_dsn(source)),
                        transfer,
                        ZoneInfo("UTC"),
                        include_tables=("child",),
                    )
                )

            self.assertFalse(transfer.exists())

    def test_import_blocks_selected_child_without_parent_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.db"
            transfer = root / "transfer.jsonl"
            self._create_schema(target)
            self._write_transfer(
                transfer,
                "child",
                (
                    ColumnDefinition("id", "INTEGER"),
                    ColumnDefinition("parent_id", "INTEGER"),
                    ColumnDefinition("note", "TEXT"),
                ),
                ("id",),
                ((10, 1, "child"),),
            )

            with self.assertRaisesRegex(DatabaseTransferError, "unselected"):
                import_database(
                    ImportOptions(
                        TransferConnection("sqlite", dsn=sqlite_dsn(target)),
                        transfer,
                        "insert",
                        ZoneInfo("UTC"),
                    )
                )

            connection = sqlite3.connect(target)
            self.assertEqual(connection.execute("SELECT * FROM child").fetchall(), [])
            connection.close()

    def test_export_rejects_an_excluded_table_not_in_source_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            transfer = root / "transfer.jsonl"
            self._create_schema(source)

            with self.assertRaisesRegex(DatabaseTransferError, "source database"):
                export_database(
                    ExportOptions(
                        TransferConnection("sqlite", dsn=sqlite_dsn(source)),
                        transfer,
                        ZoneInfo("UTC"),
                        ("missing",),
                    )
                )

    def test_import_rejects_an_excluded_table_not_in_transfer_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.db"
            transfer = root / "transfer.jsonl"
            connection = sqlite3.connect(target)
            connection.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
            connection.commit()
            connection.close()
            self._write_transfer(
                transfer,
                "users",
                (ColumnDefinition("id", "INTEGER"),),
                ("id",),
                ((1,),),
            )

            with self.assertRaisesRegex(DatabaseTransferError, "excluded table"):
                import_database(
                    ImportOptions(
                        TransferConnection("sqlite", dsn=sqlite_dsn(target)),
                        transfer,
                        "insert",
                        ZoneInfo("UTC"),
                        ("missing",),
                    )
                )

    def test_upsert_matches_a_complete_composite_primary_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.db"
            transfer = root / "transfer.jsonl"
            connection = sqlite3.connect(target)
            connection.execute(
                "CREATE TABLE pairs("
                "left_id INTEGER, right_id INTEGER, value TEXT, "
                "PRIMARY KEY(left_id, right_id))"
            )
            connection.execute("INSERT INTO pairs VALUES (1, 2, 'old')")
            connection.commit()
            connection.close()
            self._write_transfer(
                transfer,
                "pairs",
                (
                    ColumnDefinition("left_id", "INTEGER"),
                    ColumnDefinition("right_id", "INTEGER"),
                    ColumnDefinition("value", "TEXT"),
                ),
                ("left_id", "right_id"),
                ((1, 2, "new"), (1, 3, "inserted")),
            )

            import_database(
                ImportOptions(
                    TransferConnection("sqlite", dsn=sqlite_dsn(target)),
                    transfer,
                    "upsert",
                    ZoneInfo("UTC"),
                )
            )

            connection = sqlite3.connect(target)
            self.assertEqual(
                connection.execute(
                    "SELECT left_id, right_id, value FROM pairs ORDER BY right_id"
                ).fetchall(),
                [(1, 2, "new"), (1, 3, "inserted")],
            )
            connection.close()

    def test_import_preflight_rejects_later_missing_table_without_writing_first(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.db"
            transfer = root / "transfer.jsonl"
            connection = sqlite3.connect(target)
            connection.execute("CREATE TABLE kept(id INTEGER PRIMARY KEY, value TEXT)")
            connection.commit()
            connection.close()
            with transfer.open("w", encoding="utf-8") as stream:
                write_jsonl(
                    stream,
                    TransferHeader("dbtalk.database-transfer/v1", "sqlite"),
                    (
                        TableBlock(
                            TableBlockHeader("kept", (ColumnDefinition("id", "INTEGER"),), ("id",)),
                            ((1,),),
                        ),
                        TableBlock(
                            TableBlockHeader(
                                "missing", (ColumnDefinition("id", "INTEGER"),), ("id",)
                            ),
                            ((1,),),
                        ),
                    ),
                )

            with self.assertRaisesRegex(DatabaseTransferError, "does not exist"):
                import_database(
                    ImportOptions(
                        TransferConnection("sqlite", dsn=sqlite_dsn(target)),
                        transfer,
                        "insert",
                        ZoneInfo("UTC"),
                    )
                )
            connection = sqlite3.connect(target)
            self.assertEqual(connection.execute("SELECT * FROM kept").fetchall(), [])
            connection.close()

    def test_later_table_failure_rolls_back_that_block_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.db"
            transfer = root / "transfer.jsonl"
            connection = sqlite3.connect(target)
            connection.executescript(
                """
                CREATE TABLE committed(id INTEGER PRIMARY KEY, value TEXT);
                CREATE TABLE source_parent(id INTEGER PRIMARY KEY);
                CREATE TABLE rejected(
                    id INTEGER PRIMARY KEY,
                    parent_id INTEGER NOT NULL REFERENCES source_parent(id)
                );
                """
            )
            connection.commit()
            connection.close()
            with transfer.open("w", encoding="utf-8") as stream:
                write_jsonl(
                    stream,
                    TransferHeader("dbtalk.database-transfer/v1", "sqlite"),
                    (
                        TableBlock(
                            TableBlockHeader(
                                "committed",
                                (
                                    ColumnDefinition("id", "INTEGER"),
                                    ColumnDefinition("value", "TEXT"),
                                ),
                                ("id",),
                            ),
                            ((1, "kept"),),
                        ),
                        TableBlock(
                            TableBlockHeader(
                                "source_parent",
                                (ColumnDefinition("id", "INTEGER"),),
                                ("id",),
                            ),
                            (),
                        ),
                        TableBlock(
                            TableBlockHeader(
                                "rejected",
                                (
                                    ColumnDefinition("id", "INTEGER"),
                                    ColumnDefinition("parent_id", "INTEGER"),
                                ),
                                ("id",),
                            ),
                            ((1, 999),),
                        ),
                    ),
                )

            with self.assertRaisesRegex(DatabaseTransferError, "rejected"):
                import_database(
                    ImportOptions(
                        TransferConnection("sqlite", dsn=sqlite_dsn(target)),
                        transfer,
                        "insert",
                        ZoneInfo("UTC"),
                    )
                )
            connection = sqlite3.connect(target)
            self.assertEqual(
                connection.execute("SELECT * FROM committed").fetchall(), [(1, "kept")]
            )
            self.assertEqual(connection.execute("SELECT * FROM rejected").fetchall(), [])
            connection.close()

    def test_invalid_jsonl_never_creates_a_partial_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.db"
            transfer = root / "transfer.jsonl"
            connection = sqlite3.connect(target)
            connection.execute("CREATE TABLE values_table(id INTEGER PRIMARY KEY)")
            connection.commit()
            connection.close()
            transfer.write_text(
                '{"kind":"header","format":"dbtalk.database-transfer/v1","source":"sqlite"}\n'
                '{"kind":"table","name":"values_table","columns":[{"name":"id","declared_type":"INTEGER"}],"primary_key":["id"]}\n'
                '{"kind":"row","values":[1]}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(DatabaseTransferError, "missing its end"):
                import_database(
                    ImportOptions(
                        TransferConnection("sqlite", dsn=sqlite_dsn(target)),
                        transfer,
                        "insert",
                        ZoneInfo("UTC"),
                    )
                )

    def test_jsonl_value_types_and_datetime_rules(self) -> None:
        self.assertEqual(
            encode_value(datetime(2026, 8, 19, 8, 0, 0), "DATETIME", ZoneInfo("Asia/Shanghai")),
            "2026-08-19T00:00:00Z",
        )
        self.assertEqual(
            encode_value(Decimal("1.2300"), "DECIMAL(10,4)", ZoneInfo("UTC")),
            {"$type": "decimal", "value": "1.2300"},
        )
        self.assertEqual(
            decode_value("2026-08-19T00:00:00Z", "DATETIME", ZoneInfo("Asia/Shanghai")),
            "2026-08-19 08:00:00",
        )
        self.assertTrue(decode_value(1, "BOOLEAN", ZoneInfo("UTC")))
        self.assertFalse(decode_value("false", "BOOLEAN", ZoneInfo("UTC")))
        self.assertEqual(
            encode_value(
                "2026-08-19 08:00:00.123456789 +0800 CST",
                "DATETIME",
                ZoneInfo("UTC"),
            ),
            "2026-08-19T00:00:00.123456789Z",
        )
        self.assertEqual(
            decode_value(
                "2026-08-19T00:00:00.123456789Z",
                "DATETIME(6)",
                ZoneInfo("UTC"),
                datetime_precision=6,
            ),
            "2026-08-19 00:00:00.123456",
        )
        with self.assertRaisesRegex(DatabaseTransferError, "non-finite"):
            encode_value(float("nan"), "REAL", ZoneInfo("UTC"))
        with self.assertRaisesRegex(DatabaseTransferError, "datetime"):
            decode_value("2026-08-19", "DATETIME", ZoneInfo("UTC"))
        with self.assertRaisesRegex(DatabaseTransferError, "BOOLEAN"):
            decode_value(2, "BOOLEAN", ZoneInfo("UTC"))

    def test_mysql_time_duration_must_fit_a_portable_time_of_day(self) -> None:
        self.assertEqual(_mysql_time_of_day(timedelta(hours=8, minutes=9)), "08:09:00")
        with self.assertRaisesRegex(DatabaseTransferError, "time-of-day"):
            _mysql_time_of_day(timedelta(hours=24))

    def test_mysql_zero_dates_follow_export_configuration(self) -> None:
        options = ExportOptions(
            TransferConnection("mysql", dsn="mysql+pymysql://user:pass@db.test:3306/app"),
            Path("transfer.jsonl"),
            ZoneInfo("UTC"),
        )

        self.assertIsNone(_encode_mysql_value("0000-00-00", "DATE", options))
        self.assertIsNone(_encode_mysql_value("0000-00-00 00:00:00.000", "DATETIME(3)", options))
        self.assertIsNone(_encode_mysql_value("0000-00-00 00:00:00", "TIMESTAMP", options))
        self.assertEqual(
            _encode_mysql_value("0000-00-00", "VARCHAR(10)", options),
            "0000-00-00",
        )

        disabled_options = ExportOptions(
            TransferConnection("mysql", dsn="mysql+pymysql://user:pass@db.test:3306/app"),
            Path("transfer.jsonl"),
            ZoneInfo("UTC"),
            zero_datetime_as_null=False,
        )
        with self.assertRaisesRegex(DatabaseTransferError, "zero date"):
            _encode_mysql_value("0000-00-00 00:00:00", "DATETIME", disabled_options)

    def test_cli_passes_zero_date_configuration_to_mysql_export(self) -> None:
        with (
            patch(
                "dbtalk.database.cli.export_database",
                return_value=TransferSummary(table_count=0, row_count=0),
            ) as export,
            patch.dict(
                os.environ,
                {"DBTALK_MYSQL__ZERO_DATETIME_AS_NULL": "false"},
                clear=False,
            ),
        ):
            result = CliRunner().invoke(
                main,
                [
                    "export",
                    "--source",
                    "mysql",
                    "--dsn",
                    "mysql+pymysql://user:pass@db.test:3306/app",
                    "--output",
                    "transfer.jsonl",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        options = export.call_args.args[0]
        self.assertFalse(options.zero_datetime_as_null)

    def test_jsonl_reader_rejects_non_standard_numeric_constants(self) -> None:
        with self.assertRaisesRegex(DatabaseTransferError, "invalid JSONL"):
            read_jsonl(
                io.StringIO(
                    '{"kind":"header","format":"dbtalk.database-transfer/v1","source":NaN}\n'
                )
            )

    def test_writer_rejects_non_finite_values_and_unknown_type_mismatch(self) -> None:
        with self.assertRaisesRegex(DatabaseTransferError, "invalid number"):
            write_jsonl(
                io.StringIO(),
                TransferHeader("dbtalk.database-transfer/v1", "sqlite"),
                (
                    TableBlock(
                        TableBlockHeader("numbers", (ColumnDefinition("value", "REAL"),), ()),
                        ((float("nan"),),),
                    ),
                ),
            )
        self.assertTrue(compatible_types("UUID", "uuid"))
        self.assertFalse(compatible_types("UUID", "CHAR(36)"))

    def test_transfer_always_uses_the_sqlalchemy_adapter(self) -> None:
        options = ExportOptions(
            TransferConnection("mysql", dsn="mysql+pymysql://user:pass@db.test:3306/app"),
            Path("transfer.jsonl"),
            ZoneInfo("UTC"),
        )
        with patch(
            "dbtalk.database.transfer.export_sqlalchemy",
            return_value=TransferSummary(table_count=1, row_count=2),
        ) as export:
            self.assertEqual(export_database(options).row_count, 2)
        export.assert_called_once_with(options)

        import_options = ImportOptions(
            options.connection,
            Path("transfer.jsonl"),
            "upsert",
            ZoneInfo("UTC"),
        )
        with patch(
            "dbtalk.database.transfer.import_sqlalchemy",
            return_value=TransferSummary(table_count=1, row_count=2),
        ) as importer:
            self.assertEqual(import_database(import_options).row_count, 2)
        importer.assert_called_once_with(import_options)

    def test_cli_hides_mysql_dsn_value_from_errors(self) -> None:
        result = CliRunner().invoke(
            main,
            [
                "export",
                "--source",
                "mysql",
                "--output",
                "transfer.jsonl",
                "--dsn-env",
                "MISSING_TRANSFER_DSN",
            ],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("DSN environment variable is not set", result.output)
        self.assertNotIn("MISSING_TRANSFER_DSN", result.output)
        self.assertNotIn("secret", result.output)

    def test_cli_exports_and_imports_sqlite_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            target = root / "target.db"
            transfer = root / "transfer.jsonl"
            for path in (source, target):
                connection = sqlite3.connect(path)
                connection.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT)")
                connection.commit()
                connection.close()
            source_connection = sqlite3.connect(source)
            source_connection.execute("INSERT INTO users VALUES (1, 'Ada')")
            source_connection.commit()
            source_connection.close()

            runner = CliRunner()
            exported = runner.invoke(
                main,
                [
                    "export",
                    "--source",
                    "sqlite",
                    "--dsn",
                    f"sqlite:///{source.as_posix()}",
                    "--output",
                    str(transfer),
                ],
            )
            imported = runner.invoke(
                main,
                [
                    "import",
                    "--target",
                    "sqlite",
                    "--dsn",
                    f"sqlite:///{target.as_posix()}",
                    "--input",
                    str(transfer),
                    "--mode",
                    "insert",
                ],
            )

            self.assertEqual(exported.exit_code, 0, exported.output)
            self.assertEqual(imported.exit_code, 0, imported.output)
            self.assertIn("1 tables, 1 rows", exported.output)
            target_connection = sqlite3.connect(target)
            self.assertEqual(
                target_connection.execute("SELECT * FROM users").fetchall(),
                [(1, "Ada")],
            )
            target_connection.close()

    def test_cli_export_uses_default_timestamped_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.db"
            connection = sqlite3.connect(source)
            connection.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT)")
            connection.execute("INSERT INTO users VALUES (1, 'Ada')")
            connection.commit()
            connection.close()

            runner = CliRunner()
            with runner.isolated_filesystem():
                default_export = runner.invoke(
                    main,
                    [
                        "export",
                        "--source",
                        "sqlite",
                        "--dsn",
                        f"sqlite:///{source.as_posix()}",
                    ],
                )
                default_outputs = list((Path.cwd() / "data").glob("sqlite-*.jsonl"))
                Path("exports").mkdir()
                directory_export = runner.invoke(
                    main,
                    [
                        "export",
                        "--source",
                        "sqlite",
                        "--dsn",
                        f"sqlite:///{source.as_posix()}",
                        "--output",
                        "exports",
                    ],
                )
                directory_outputs = list((Path.cwd() / "exports").glob("sqlite-*.jsonl"))

            self.assertEqual(default_export.exit_code, 0, default_export.output)
            self.assertEqual(directory_export.exit_code, 0, directory_export.output)
            self.assertEqual(len(default_outputs), 1)
            self.assertEqual(len(directory_outputs), 1)
            self.assertIn(str(default_outputs[0].resolve()), default_export.output)
            self.assertIn(str(directory_outputs[0].resolve()), directory_export.output)

    @staticmethod
    def _create_schema(path: Path) -> None:
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE parent(
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                amount DECIMAL,
                happened DATETIME,
                day DATE,
                at TIME,
                payload BLOB
            );
            CREATE TABLE child(
                id INTEGER PRIMARY KEY,
                parent_id INTEGER NOT NULL REFERENCES parent(id),
                note TEXT
            );
            """
        )
        connection.commit()
        connection.close()

    @staticmethod
    def _write_transfer(
        path: Path,
        name: str,
        columns: tuple[ColumnDefinition, ...],
        primary_key: tuple[str, ...],
        rows: tuple[tuple[JSONValue, ...], ...],
    ) -> None:
        with path.open("w", encoding="utf-8") as stream:
            write_jsonl(
                stream,
                TransferHeader("dbtalk.database-transfer/v1", "sqlite"),
                (
                    TableBlock(
                        TableBlockHeader(name, columns, primary_key),
                        rows,
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()


class _FakeMysqlCursor:
    def __init__(self, connection: _FakeMysqlConnection) -> None:
        self.connection = connection
        self.query = ""
        self._rows_read = False

    def __enter__(self) -> _FakeMysqlCursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, query: str, parameters: tuple[object, ...] | None = None) -> None:
        self.query = query
        self.connection.calls.append((query, parameters))

    def fetchall(self) -> list[tuple[object, ...]]:
        if "information_schema.COLUMNS" in self.query:
            return [("users", "id", "bigint"), ("users", "name", "varchar(255)")]
        if "CONSTRAINT_NAME = 'PRIMARY'" in self.query:
            return [("users", "id")]
        if "REFERENCED_TABLE_NAME" in self.query:
            return []
        return self.connection.rows

    def fetchmany(self, size: int) -> list[tuple[object, ...]]:
        self.connection.fetchmany_sizes.append(size)
        if self._rows_read:
            return []
        self._rows_read = True
        return self.connection.rows

    def fetchone(self) -> tuple[int] | None:
        return (1,) if self.connection.exists and self.query.startswith("SELECT 1") else None


class _FakeMysqlConnection:
    def __init__(self, exists: bool, rows: list[tuple[object, ...]] | None = None) -> None:
        self.exists = exists
        self.rows = rows or []
        self.calls: list[tuple[str, tuple[object, ...] | None]] = []
        self.fetchmany_sizes: list[int] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self, _cursor_class: object | None = None) -> _FakeMysqlCursor:
        return _FakeMysqlCursor(self)

    def begin(self) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True
