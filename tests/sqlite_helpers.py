"""Named SQLite memory databases shared by independent test connections."""

from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from sqlite3 import Connection, connect
from uuid import uuid4


@dataclass(frozen=True)
class MemoryDatabase:
    uri: str

    @property
    def dsn(self) -> str:
        return f"sqlite:///{self.uri}&uri=true"

    def connect(self) -> Connection:
        return connect(self.uri, uri=True)


@contextmanager
def memory_database() -> Iterator[MemoryDatabase]:
    database = MemoryDatabase(f"file:dbtalk-test-{uuid4().hex}?mode=memory&cache=shared")
    # Keep the database alive while production clients open and dispose their own connections.
    with closing(database.connect()):
        yield database
