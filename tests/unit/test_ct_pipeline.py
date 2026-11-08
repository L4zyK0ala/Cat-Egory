import asyncio
import json

from integrations.brand_watch import default_watchlist
from integrations.ct_watch import Pipeline
from integrations.lookalike import TopIndex


class FakeStream:
    def __init__(self, messages):
        self.messages = messages

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for m in self.messages:
            yield m


def msg(*names):
    return json.dumps({"message_type": "dns_entries", "data": list(names)})


def run_pipeline(messages, *, queue_size=50_000, top=None):
    stored = []

    async def main():
        pipe = Pipeline(default_watchlist(), lambda hits: (stored.extend(hits), len(hits))[1], queue_size=queue_size,
                        top=top)
        await pipe.read(FakeStream(messages))  # read everything first: worst-case burst
        tasks = [asyncio.create_task(pipe.match()), asyncio.create_task(pipe.write())]
        while pipe.messages.qsize() or pipe.hits.qsize():
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        for t in tasks:
            t.cancel()
        return pipe.stats

    return asyncio.run(main()), stored


def test_pipeline_matches_and_stores():
    top = TopIndex.build(["amazon.com", "google.com"])
    stats, stored = run_pipeline([msg("www.google.com", "garantibbva-giris.com"), "not json", msg(),
                                  json.dumps({"data": "x"}), msg("amaz0n.net", "cdn.amazon.com", 42)], top=top)
    assert (stats.messages, stats.names, stats.hits, stats.queued, stats.dropped) == (5, 4, 2, 2, 0)  # 42 is no name
    assert stored == [("garantibbva-giris.com", "Garanti BBVA (bank)"), ("amaz0n.net", "amazon.com (homograph)")]


def test_full_queue_drops_are_counted_not_blocking():
    stats, _ = run_pipeline([msg("a.example.com")] * 50, queue_size=10)
    assert stats.messages == 50 and stats.dropped == 40 and stats.names == 10


def test_protected_fast_path():
    top = TopIndex.build(["googleusercontent.com"])
    assert top.protected("abc123.googleusercontent.com") and not top.protected("googleusercontent.co")
    assert top.check("x.googleusercontent.com") is None
