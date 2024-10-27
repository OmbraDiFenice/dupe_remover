import os
import hashlib
import sqlite3
import re
import dataclasses
import logging
import json
from typing import Iterator
from dataclasses_json import DataClassJsonMixin


LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass
class Duplicate(DataClassJsonMixin):
    content_hash: str
    files: list[str]


@dataclasses.dataclass
class DeletionEntry(DataClassJsonMixin):
    duplicate: Duplicate
    to_keep: str

    @property
    def to_delete(self) -> Iterator[str]:
        for filename in self.duplicate.files:
            if filename != self.to_keep:
                yield filename

    def format_for_output(self) -> str:
        nl = "\n"
        return f"""
# duplicates of: {self.to_keep}
# hash: {self.duplicate.content_hash}
{nl.join(file for file in self.duplicate.files if file != self.to_keep)}
"""


class Session:
    def __init__(self, session_dir: str) -> None:
        self._session_dir = session_dir

    @property
    def session_dir(self) -> str:
        return self._session_dir

    @property
    def db(self) -> str:
        return f"{self._session_dir}/hashes.db"

    @property
    def queue_file(self) -> str:
        return f"{self._session_dir}/deletion_queue.json"


class Storage:
    def __init__(self, session: Session) -> None:
        self._session = session

    def reset(self) -> None:
        """Should only be called by find_clones.App"""

        if os.path.exists(self._session.db):
            os.unlink(self._session.db)

        conn = self._connect()
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS hashes (filename TEXT, hash TEXT)")
        conn.commit()

    def store_file(self, filename: str) -> None:
        if not self._is_supported(filename):
            LOGGER.info("skipping %s", filename)
            return

        file_hash = self._hash_file(filename)

        conn = self._connect()
        c = conn.cursor()
        c.execute("INSERT INTO hashes VALUES (?, ?)", (filename, file_hash))
        conn.commit()

    def find_duplicates(self) -> list[Duplicate]:
        conn = self._connect()
        c = conn.cursor()
        c.execute("SELECT hash FROM hashes GROUP BY hash HAVING COUNT(hash) > 1")
        duplicate_hashes = c.fetchall()

        return [
            Duplicate(
                content_hash=duplicate_hash[0],
                files=self._find_all_with_hash(duplicate_hash[0]),
            )
            for duplicate_hash in duplicate_hashes
        ]

    def remove(self, duplicate: Duplicate) -> None:
        conn = self._connect()
        c = conn.cursor()
        for filename in duplicate.files:
            c.execute("DELETE FROM hashes WHERE filename = ?", (filename,))
        conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._session.db)

    def _find_all_with_hash(self, hash_to_find: str) -> list[str]:
        conn = self._connect()
        c = conn.cursor()
        c.execute(
            "SELECT filename FROM hashes WHERE hash = ? ORDER BY filename",
            (hash_to_find,),
        )
        return [res[0] for res in c.fetchall()]

    def _hash_file(self, filename: str) -> str:
        h = hashlib.sha256()
        with open(filename, "rb") as file:
            for chunk in iter(lambda: file.read(4096), b""):
                h.update(chunk)
        return h.hexdigest()

    def _is_supported(self, filename: str) -> bool:
        return filename.rsplit(".", 1)[-1].lower() in (
            "png",
            "jpg",
            "jpeg",
            "tiff",
            "bmp",
            "gif",
        )


class DeletionQueue:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._queue: list[DeletionEntry] = []
        self._index: dict[str, DeletionEntry] = {}

    def get_by_hash(self, content_hash: str) -> DeletionEntry | None:
        return self._index.get(content_hash)

    def clear_deletion_queue(self) -> None:
        self._queue.clear()
        self._index.clear()

    def add(self, duplicate: Duplicate, to_keep: str) -> None:
        entry = self.get_by_hash(duplicate.content_hash)
        if entry is not None:
            entry.to_keep = to_keep
            return

        entry = DeletionEntry(duplicate=duplicate, to_keep=to_keep)
        self._add_new(entry)

    def _add_new(self, entry: DeletionEntry) -> None:
        self._queue.append(entry)
        self._index[entry.duplicate.content_hash] = entry

    def remove(self, duplicate: Duplicate) -> None:
        entry = self.get_by_hash(duplicate.content_hash)
        if entry is None:
            return

        self._queue.remove(entry)
        del self._index[duplicate.content_hash]

    def preview_delete_queue(self) -> str:
        return "\n".join([entry.format_for_output() for entry in self._queue])

    def persist(self) -> None:
        with open(self._session.queue_file, "w") as f:
            json.dump([entry.to_dict() for entry in self._queue], f)

    def __iter__(self) -> Iterator[DeletionEntry]:
        return iter(self._queue)

    @staticmethod
    def load(session: Session) -> "DeletionQueue":
        # pylint: disable=protected-access
        ret = DeletionQueue(session)
        with open(session.queue_file) as f:
            data = json.load(f)
            for entry in data:
                ret._add_new(DeletionEntry.from_dict(entry))
        return ret


class App:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._storage = Storage(self._session)
        self._delete_queue = DeletionQueue(self._session)

    def analyze_dir(self, top_dir: str) -> None:
        self._storage.reset()
        self._delete_queue.clear_deletion_queue()

        for root, _, files in os.walk(top_dir):
            for file in files:
                full_file_path = os.path.join(root, file)
                LOGGER.info("processing %s", full_file_path)
                self._storage.store_file(full_file_path)

        LOGGER.info("done")

    def do_delete_queued_files(self) -> None:
        for entry in self._delete_queue:
            for filename in entry.to_delete:
                LOGGER.info("deleting %s", filename)
                if os.path.exists(filename):
                    try:
                        os.unlink(filename)
                    except Exception as e:  # pylint: disable=broad-except
                        LOGGER.warning("could not delete %s: %s", filename, e)
                else:
                    LOGGER.warning("could not delete %s: does not exist", filename)
            self._delete_queue.remove(entry.duplicate)
            self._storage.remove(entry.duplicate)

    def clear_deletion_queue(self) -> None:
        self._delete_queue.clear_deletion_queue()

    def get_all_duplicates(self) -> list[Duplicate]:
        return self._storage.find_duplicates()

    def queue_for_deletion(self, duplicate: Duplicate, to_keep: str) -> None:
        self._delete_queue.add(duplicate, to_keep)

    def get_queued_deletion_entry_for(self, duplicate: Duplicate) -> DeletionEntry | None:
        return self._delete_queue.get_by_hash(duplicate.content_hash)

    def remove_from_deletion_queue(self, duplicate: Duplicate) -> None:
        self._delete_queue.remove(duplicate)

    def preview_deletion_queue(self) -> str:
        return self._delete_queue.preview_delete_queue()

    @property
    def session(self) -> Session:
        """Use load_session to set a new session"""
        return self._session

    def save_session(self) -> None:
        self._delete_queue.persist()

    def load_session(self, session: Session) -> None:
        self._session = session
        self._storage = Storage(self._session)
        self._delete_queue = DeletionQueue.load(self._session)

    def print_dupes(self, remove_prefix: str = "") -> None:
        reg = re.compile(f"^{remove_prefix}")

        print("\nDuplicate files:\n")
        for dupe in self._storage.find_duplicates():
            for filename in dupe.files:
                print(f"{reg.sub('', filename)}\n")
