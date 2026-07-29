"""戰果評估 BDA（WP-C10.4b）——**這是情報，不是事實**。

整組測試守的就是那一句：回報的數字不等於真值，沒有觀測就沒有回報，
而且回報本身不能反過來變成新的洩漏管道。
"""

from __future__ import annotations

from app.adjudication.bda import BDA_ERROR_BAND, build_bda_event, estimate_losses
from app.engine.rng import DeterministicRNG
from app.state.broadcaster import build_event_envelope, event_audience

_AIM = (24.0, 121.0)


def _rng(stream: str = "bda", seed: int = 5) -> DeterministicRNG:
    return DeterministicRNG(master_seed=seed, stream_id=stream)


def _event(truth: float = 40.0, faction: str = "BLUE", **kw: object) -> object:
    return build_bda_event(
        tick=12,
        shooter_id="ARTY",
        shooter_faction=faction,
        aim=_AIM,
        truth=truth,
        rng=_rng(),
        order_id="o1",
        **kw,  # type: ignore[arg-type]
    )


# ---- 誤差 ----


def test_the_estimate_is_not_the_truth() -> None:
    """**這條就是本卡的全部理由。**回傳真值的 BDA 不是 BDA，只是把帳本換個名字再印一次。"""
    truth = 40.0
    estimates = {estimate_losses(truth, _rng(seed=s)) for s in range(20)}
    assert estimates != {truth}, "每次都回真值＝根本沒有迷霧"
    assert any(e != truth for e in estimates)


def test_the_estimate_stays_within_the_declared_band() -> None:
    """誤差要在**寫在事件裡的**帶寬內——不是隨便亂給，讀者才能判斷它多不可靠。"""
    truth = 100.0
    for seed in range(50):
        est = estimate_losses(truth, _rng(seed=seed))
        assert truth * (1 - BDA_ERROR_BAND) - 0.1 <= est <= truth * (1 + BDA_ERROR_BAND) + 0.1


def test_the_estimate_is_deterministic() -> None:
    """同一顆種子必得同一個估計（紅線 1）。"""
    assert estimate_losses(40.0, _rng()) == estimate_losses(40.0, _rng())


def test_zero_truth_estimates_zero() -> None:
    """一個都沒打到就是沒打到——不要無中生有一個「大概傷了 3 點」。"""
    assert estimate_losses(0.0, _rng()) == 0.0


def test_it_never_goes_negative() -> None:
    assert estimate_losses(0.5, _rng(seed=99)) >= 0.0


def test_estimate_precision_differs_from_the_ledger() -> None:
    """小數一位 vs 帳本的三位——**一眼看得出這是估計不是量出來的數**。"""
    est = estimate_losses(37.777, _rng())
    assert est == round(est, 1)


# ---- 事件形狀：每個留空的欄位都堵一個洞 ----


def test_damage_calc_is_none() -> None:
    """`aar/stats.py` 對**每一種**事件都做 `total_damage += damage_calc`。

    估計值填進去會被加在真值上——AAR 的總戰損直接變成兩倍多一點的胡說。
    """
    assert _event().damage_calc is None  # type: ignore[attr-defined]


def test_target_id_is_none() -> None:
    """真實單位身分不得進 BDA：那會流進 AI briefing，也會覆蓋 observer_faction 的受眾意圖。"""
    assert _event().target_id is None  # type: ignore[attr-defined]


def test_only_the_firing_faction_receives_it() -> None:
    """受眾唯一。**少了 `observer_faction`，`event_audience` 會退回全域廣播**
    ——挨打的一方就會收到別人對自己的戰果評估。"""
    assert event_audience(_event(), lambda uid: "BLUE") == ["BLUE"]  # type: ignore[arg-type]


def test_no_per_unit_breakdown() -> None:
    """逐單位 BDA 等於把敵軍編成表交給射方。真實的 BDA 本來也是「那一片大概掉了多少」。"""
    dec = _event().ai_decision  # type: ignore[attr-defined]
    assert "losses_by_unit" not in dec
    assert "estimated_losses" in dec


def test_it_is_flagged_as_an_estimate() -> None:
    dec = _event().ai_decision  # type: ignore[attr-defined]
    assert dec["is_estimate"] is True
    assert dec["error_band"] == BDA_ERROR_BAND


def test_the_envelope_carries_the_estimate_and_its_flag() -> None:
    """前端要能同時拿到數字**與**「這是估計」的旗標——只給數字就會被畫成真值。"""
    payload = build_event_envelope(_event(), lambda uid: "BLUE")["payload"]  # type: ignore[arg-type]
    assert payload["is_estimate"] is True
    assert payload["error_band"] == BDA_ERROR_BAND
    assert "estimated_losses" in payload
    assert "damage" not in payload  # damage_calc 是 None → 不會有這個鍵
