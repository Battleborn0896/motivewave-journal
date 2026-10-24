"""One background file watcher per database, shared by every rerun and browser tab of this process.

Streamlit runs the script again on every click, so the watcher cannot live in the script. It lives in
st.cache_resource, keyed by the database it writes to, and is stopped when the process exits so a
shutdown never lands in the middle of an import.
"""

from __future__ import annotations

import atexit
import threading

import streamlit as st

from mwjournal import Journal
from mwjournal.sync import SyncWorker, ensure_default_connection

_running: dict[str, SyncWorker] = {}
_lock = threading.Lock()


# validate: a watcher that was stopped is replaced on the next call, never handed back dead.
@st.cache_resource(show_spinner=False, validate=lambda worker: worker.thread.is_alive())
def _worker(path: str, dataset: str) -> SyncWorker:
    with _lock:
        # The cache can be cleared under a running app; the database must still have one watcher.
        old = _running.pop(path, None)
        if old is not None:
            old.stop()
        # The environment's own journal, which Replay's watcher needs: it reads the tags, from Live, for Tag My Next
        # Trade. `path` is that journal's file, the key it is kept under.
        worker = _running[path] = SyncWorker(Journal(dataset=dataset)).start()
    return worker


def start(dataset: str = 'live') -> SyncWorker:
    """Watch the study's bridge file for this environment; nothing has to be set up first."""
    journal = Journal(dataset=dataset)
    ensure_default_connection(journal)
    return _worker(str(journal.path), dataset)


def stop_all() -> None:
    with _lock:
        workers = list(_running.values())
        _running.clear()
    for worker in workers:
        worker.stop()


atexit.register(stop_all)
