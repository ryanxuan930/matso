"""發佈 EVENT 到 session Redis stream（O4.6）——fakeredis。"""

from __future__ import annotations

import json

from fakeredis import FakeStrictRedis

from app.stream.publish import publish_event


def test_publish_event_assigns_seq_and_rings() -> None:
    r = FakeStrictRedis(decode_responses=True)
    seq1 = publish_event(r, "s1", "ENGAGEMENT_RESOLVED", {"order_id": "o1"}, faction="BLUE")
    seq2 = publish_event(r, "s1", "ORDER_VALIDATED", {"order_id": "o2"})
    assert (seq1, seq2) == (1, 2)  # per-session INCR

    ring = r.lrange("session:s1:ring", 0, -1)
    assert len(ring) == 2
    first = json.loads(ring[0])
    assert first["type"] == "EVENT"
    assert first["seq"] == 1
    assert first["faction"] == "BLUE"
    assert first["payload"]["event_type"] == "ENGAGEMENT_RESOLVED"
    assert first["payload"]["order_id"] == "o1"
    # 無 faction 者不帶 faction 欄（廣播全體）
    assert "faction" not in json.loads(ring[1])
