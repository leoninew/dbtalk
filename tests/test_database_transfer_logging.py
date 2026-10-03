from __future__ import annotations

import logging
import sqlite3
import tempfile
import unittest
from datetime import UTC
from itertools import count
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from dbtalk.database import sqlalchemy_transfer as adapter
from dbtalk.database.dsn import sqlite_dsn
from dbtalk.database.format import TRANSFER_FORMAT, write_document
from dbtalk.database.transfer import (
    ColumnDefinition,
    DatabaseTransferError,
    ExportOptions,
    ImportOptions,
    TableBlock,
    TableBlockHeader,
    TransferConnection,
    TransferDocument,
    TransferHeader,
    export_database,
    import_database,
)
from dbtalk.logging_config import configure_logging
from dbtalk.settings import LoggingSettings


class DatabaseTransferLoggingTests(unittest.TestCase):
    def test_configure_logging_uses_configured_standard_format_and_level(self) -> None:
        config = LoggingSettings(
            level="WARNING",
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

        with patch("dbtalk.logging_config.logging.basicConfig") as configure:
            configure_logging(config.level, config.format, verbose=False)

        configure.assert_called_once_with(
            level="WARNING",
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            force=True,
        )

    def test_sqlite_export_logs_task_schema_table_and_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            output = root / "transfer.jsonl"
            self._create_database(source, "source")

            with self.assertLogs("dbtalk", level="INFO") as captured:
                export_database(
                    ExportOptions(
                        TransferConnection("sqlite", dsn=sqlite_dsn(source)),
                        output,
                        ZoneInfo("UTC"),
                    )
                )

            messages = "\n".join(captured.output)
            self.assertIn("database export started driver=sqlite", messages)
            self.assertIn("database export connecting sqlite", messages)
            self.assertIn(
                "database export table completed dialect=sqlite table=items rows=1", messages
            )
            self.assertIn("database export completed driver=sqlite", messages)
            self.assertIn("written_rows=1 total_rows=unknown", messages)
            self.assertIn("status=table_completed", messages)

    def test_sqlite_import_logs_document_preflight_table_and_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            target = root / "target.db"
            output = root / "transfer.jsonl"
            self._create_database(source, "source")
            self._create_database(target, "target")
            export_database(
                ExportOptions(
                    TransferConnection("sqlite", dsn=sqlite_dsn(source)),
                    output,
                    ZoneInfo("UTC"),
                )
            )

            with self.assertLogs("dbtalk", level="INFO") as captured:
                import_database(
                    ImportOptions(
                        TransferConnection("sqlite", dsn=sqlite_dsn(target)),
                        output,
                        "upsert",
                        ZoneInfo("UTC"),
                    )
                )

            messages = "\n".join(captured.output)
            self.assertIn("database import started driver=sqlite", messages)
            self.assertIn("database import connecting sqlite", messages)
            self.assertIn(
                "database import table completed dialect=sqlite table=items rows=1 mode=upsert",
                messages,
            )
            self.assertIn("database import completed driver=sqlite", messages)
            self.assertIn("executed_rows=1 total_rows=1 pending_rows=0 committed_rows=1", messages)
            self.assertIn("status=committed", messages)

    @staticmethod
    def _create_database(path: Path, value: str) -> None:
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO items VALUES (1, ?)", (value,))
        connection.commit()
        connection.close()


def test_import_progress_interval_and_committed_input_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    target = tmp_path / "target.db"
    with sqlite3.connect(target) as connection:
        connection.execute("CREATE TABLE items(id INTEGER PRIMARY KEY)")
    path = tmp_path / "transfer.jsonl"
    write_document(
        path,
        TransferDocument(
            TransferHeader(TRANSFER_FORMAT, "sqlite"),
            (
                TableBlock(
                    TableBlockHeader("items", (ColumnDefinition("id", "INTEGER"),), ("id",)),
                    ((1,), (1,), (2,), (2,), (3,)),
                ),
            ),
        ),
    )
    monkeypatch.setattr(adapter, "BATCH_SIZE", 2)
    ticks = iter([0.0, 4.0, 5.0, 6.0, 7.0])
    monkeypatch.setattr(adapter, "monotonic", lambda: next(ticks))

    with caplog.at_level(logging.INFO, logger="dbtalk"):
        import_database(
            ImportOptions(TransferConnection("sqlite", dsn=sqlite_dsn(target)), path, "upsert", UTC)
        )

    progress = [message for message in caplog.messages if "database import progress" in message]
    assert len(progress) == 2
    assert "executed_rows=4 total_rows=5 pending_rows=4 committed_rows=0" in progress[0]
    assert "rows_per_second=0.80 elapsed_seconds=5.00 status=pending" in progress[0]
    assert "executed_rows=5 total_rows=5 pending_rows=0 committed_rows=5" in progress[1]
    assert "status=committed" in progress[1]
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT COUNT(*) FROM items").fetchone() == (3,)


def test_multiple_batches_roll_back_table_and_keep_previous_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    target = tmp_path / "target.db"
    with sqlite3.connect(target) as connection:
        connection.executescript(
            "CREATE TABLE a_kept(id INTEGER PRIMARY KEY);"
            "CREATE TABLE z_items(id INTEGER PRIMARY KEY);"
        )
    path = tmp_path / "transfer.jsonl"
    write_document(
        path,
        TransferDocument(
            TransferHeader(TRANSFER_FORMAT, "sqlite"),
            (
                TableBlock(
                    TableBlockHeader("a_kept", (ColumnDefinition("id", "INTEGER"),), ("id",)),
                    ((9,),),
                ),
                TableBlock(
                    TableBlockHeader("z_items", (ColumnDefinition("id", "INTEGER"),), ("id",)),
                    ((1,), (2,), (3,), (4,), (1,)),
                ),
            ),
        ),
    )
    monkeypatch.setattr(adapter, "BATCH_SIZE", 2)
    ticks = count(0.0, 5.0)
    monkeypatch.setattr(adapter, "monotonic", lambda: next(ticks))

    with (
        caplog.at_level(logging.INFO, logger="dbtalk"),
        pytest.raises(DatabaseTransferError, match="z_items"),
    ):
        import_database(
            ImportOptions(TransferConnection("sqlite", dsn=sqlite_dsn(target)), path, "insert", UTC)
        )

    progress = [message for message in caplog.messages if "database import progress" in message]
    assert "executed_rows=4 total_rows=5 pending_rows=4 committed_rows=1" in progress[-2]
    assert "executed_rows=4 total_rows=5 pending_rows=0 committed_rows=1" in progress[-1]
    assert "status=rolled_back" in progress[-1]
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT id FROM a_kept").fetchall() == [(9,)]
        assert connection.execute("SELECT id FROM z_items").fetchall() == []


if __name__ == "__main__":
    unittest.main()
