"""One store used from many threads at once, as the UI and the MCP server do."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from docbuilders import Prose, build, save
from fakes import FakeEmbedder

from kestrel.contracts import AuditEntry, SearchScope
from kestrel.retrieval import open_retriever
from kestrel.store import Store

SCOPE = SearchScope(collections=("alpha",))


def test_concurrent_searches_reads_and_writes(store: Store, embedder: FakeEmbedder) -> None:
    for n in range(5):
        save(store, build("alpha", f"d{n}.md", [Prose(("H",), f"waiting period text number {n} option b")]),
             embedder=embedder)
    retriever = open_retriever(store, embedder)
    expected = {h.chunk_id for h in retriever.search("option b waiting period", SCOPE, 10).hits}
    stop = threading.Event()

    def writer() -> None:
        # Writes in another thread while searches run: documents in another collection and audit rows.
        i = 0
        while not stop.is_set() and i < 20:
            save(store, build("beta", f"w{i}.md", [Prose(("H",), "option b waiting period elsewhere")]), embedder=embedder)
            store.audit.append(AuditEntry(trace_id=f"w{i}", bot_id="b", channel="ui", user="u"))
            i += 1

    def search(_: int) -> set[str]:
        return {h.chunk_id for h in retriever.search("option b waiting period", SCOPE, 10).hits}

    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(search, range(40)))
    stop.set()
    writer_thread.join()

    assert all(result == expected for result in results)
    assert store.audit.verify().ok
