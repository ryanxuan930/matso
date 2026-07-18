import pytest

from app.engine.rng import DeterministicRNG


def _draw(rng: DeterministicRNG, n: int = 20) -> list[float]:
    return [rng.random() for _ in range(n)]


def test_same_seed_same_stream_reproducible() -> None:
    # golden replay 的核心保證：獨立建構、相同參數 → 完全相同序列（P4）
    a = DeterministicRNG(master_seed=12345, stream_id="adjudication")
    b = DeterministicRNG(master_seed=12345, stream_id="adjudication")
    assert _draw(a, 100) == _draw(b, 100)


def test_different_stream_different_sequence() -> None:
    a = DeterministicRNG(master_seed=12345, stream_id="adjudication")
    b = DeterministicRNG(master_seed=12345, stream_id="sensors")
    assert _draw(a) != _draw(b)


def test_different_seed_different_sequence() -> None:
    a = DeterministicRNG(master_seed=1, stream_id="adjudication")
    b = DeterministicRNG(master_seed=2, stream_id="adjudication")
    assert _draw(a) != _draw(b)


def test_streams_are_independent() -> None:
    """驗收條件：先抽 A 再抽 B ＝ 只抽 B 的結果不變（stream 不共用狀態）。"""
    b_alone = DeterministicRNG(master_seed=999, stream_id="sensors")
    b_alone_seq = _draw(b_alone, 50)

    a = DeterministicRNG(master_seed=999, stream_id="adjudication")
    b_after_a = DeterministicRNG(master_seed=999, stream_id="sensors")
    _draw(a, 37)  # 先大量消耗 A
    b_after_a_seq = _draw(b_after_a, 50)

    assert b_after_a_seq == b_alone_seq


def test_random_range() -> None:
    rng = DeterministicRNG(master_seed=7, stream_id="test")
    for _ in range(1000):
        value = rng.random()
        assert 0.0 <= value < 1.0


def test_uniform_range() -> None:
    rng = DeterministicRNG(master_seed=7, stream_id="test")
    for _ in range(1000):
        value = rng.uniform(-5.0, 5.0)
        assert -5.0 <= value < 5.0


def test_uniform_reproducible() -> None:
    a = DeterministicRNG(master_seed=7, stream_id="test")
    b = DeterministicRNG(master_seed=7, stream_id="test")
    assert [a.uniform(0, 100) for _ in range(50)] == [b.uniform(0, 100) for _ in range(50)]


def test_choice_returns_element() -> None:
    rng = DeterministicRNG(master_seed=7, stream_id="test")
    options = ["alpha", "bravo", "charlie", "delta"]
    for _ in range(100):
        assert rng.choice(options) in options


def test_choice_reproducible() -> None:
    options = list(range(10))
    a = DeterministicRNG(master_seed=7, stream_id="test")
    b = DeterministicRNG(master_seed=7, stream_id="test")
    assert [a.choice(options) for _ in range(50)] == [b.choice(options) for _ in range(50)]


def test_choice_empty_raises() -> None:
    rng = DeterministicRNG(master_seed=7, stream_id="test")
    with pytest.raises(ValueError, match="空序列"):
        rng.choice([])


def test_empty_stream_id_rejected() -> None:
    with pytest.raises(ValueError, match="stream_id"):
        DeterministicRNG(master_seed=7, stream_id="")


def test_negative_master_seed_supported() -> None:
    # Prisma BigInt 為有號；負 seed 也須決定性
    a = DeterministicRNG(master_seed=-42, stream_id="test")
    b = DeterministicRNG(master_seed=-42, stream_id="test")
    assert _draw(a) == _draw(b)
