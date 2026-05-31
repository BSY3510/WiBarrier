"""S03 · 신호 처리 단위 테스트 (모션 스코어 + 거리)."""

from __future__ import annotations

import pytest

from wibarrier.collect.base import Sample
from wibarrier.config import Config
from wibarrier.signal import motion
from wibarrier.signal.distance import DistanceModel
from wibarrier.signal.motion import MotionDetector


# ── 순수 함수 ────────────────────────────────────────────────────────────────

def test_variability_constant_is_zero():
    assert motion.variability([-40, -40, -40]) == 0.0


def test_variability_increases_with_spread():
    assert motion.variability([-50, -30, -50, -30]) > motion.variability([-41, -40, -41, -40])


def test_variability_too_few_samples():
    assert motion.variability([-40]) == 0.0


def test_mad_robust():
    assert motion.mad([-40, -40, -40]) == 0.0
    assert motion.mad([-40, -41, -39, -40]) > 0.0


def test_strength_weight_floor():
    assert motion.strength_weight(-30, -85) == 55.0
    assert motion.strength_weight(-90, -85) == 0.0  # floor 이하 → 0


def test_aggregate_variability_weighted():
    # 강한 AP(σ=4, w=50)가 약한 AP(σ=0, w=5)보다 지배적
    agg = motion.aggregate_variability([(4.0, 50.0), (0.0, 5.0)])
    assert 3.0 < agg <= 4.0


def test_aggregate_zero_weight():
    assert motion.aggregate_variability([(4.0, 0.0)]) == 0.0


@pytest.mark.parametrize("sigma,expected", [(0.6, 0.0), (4.1, 100.0), (2.35, 50.0)])
def test_to_score_bounds(sigma, expected):
    assert motion.to_score(sigma) == pytest.approx(expected, abs=1.0)


def test_level_of():
    assert motion.level_of(10) == "low"
    assert motion.level_of(50) == "mid"
    assert motion.level_of(80) == "high"


# ── 거리 ─────────────────────────────────────────────────────────────────────

def test_distance_at_reference_is_one_meter():
    assert DistanceModel(a_dbm=-40, n=3.0).estimate(-40) == 1.0


def test_distance_monotonic_decreasing_with_stronger_rssi():
    m = DistanceModel()
    assert m.estimate(-30) < m.estimate(-50) < m.estimate(-70)


def test_distance_clamped():
    m = DistanceModel()
    assert m.estimate(10) >= 0.1        # 매우 강한 신호도 하한
    assert m.estimate(-200) <= 100.0    # 매우 약한 신호도 상한


def test_distance_calibrate_sets_reference_to_one_meter():
    m = DistanceModel.calibrate([-45, -44, -46], n=3.0)  # 중앙값 -45
    assert m.a_dbm == -45.0
    assert m.estimate(-45) == 1.0        # 기준 RSSI → 1m
    assert m.estimate(-45) < m.estimate(-65)  # 단조 유지


def test_distance_calibrate_empty_keeps_default():
    m = DistanceModel.calibrate([], n=2.5)
    assert m.a_dbm == DistanceModel().a_dbm and m.n == 2.5


# ── MotionDetector (정적 vs 동적) ────────────────────────────────────────────

def _feed(detector: MotionDetector, rssis: list[int], bssid="ap1", t0=0.0, dt=0.3):
    """rssi 시퀀스를 한 틱당 1샘플로 주입하고 마지막 결과를 반환."""
    res = {}
    for i, r in enumerate(rssis):
        s = Sample(bssid=bssid, ssid="X", rssi=r, freq=2437, ts=t0 + i * dt)
        res = detector.update([s])
    return res


def test_static_input_low_score_no_event():
    d = MotionDetector(Config(motion_window_s=8.0, min_rssi_dbm=-85, motion_threshold=40))
    res = _feed(d, [-40] * 30)
    assert res["score"] < 20
    assert res["event"] is None


def test_dynamic_input_high_score_and_event():
    d = MotionDetector(Config(motion_window_s=8.0, min_rssi_dbm=-85, motion_threshold=40))
    # 경로 차단 모사: 큰 진폭 변동 (-30 ↔ -50)
    res = _feed(d, [-30, -50] * 20)
    assert res["score"] >= 40
    # 시퀀스 도중 한 번 이상 이벤트가 발생했어야 함
    assert d.active or res["event"] is not None


def test_event_hysteresis_no_chatter():
    d = MotionDetector(Config(motion_window_s=8.0, min_rssi_dbm=-85, motion_threshold=40))
    events = []
    for i in range(40):
        r = -30 if i % 2 == 0 else -50
        s = Sample(bssid="ap1", ssid="X", rssi=r, freq=2437, ts=i * 0.3)
        out = d.update([s])
        if out["event"]:
            events.append(i)
    # 히스테리시스로 연속 발생(매 틱 이벤트)이 아니어야 함
    assert len(events) <= 3


def test_motion_detector_exposes_per_ap_and_rssi():
    d = MotionDetector(Config(motion_window_s=8.0, min_rssi_dbm=-85))
    res = _feed(d, [-30, -50] * 20, bssid="ap1")
    assert "ap1" in res["per_ap"] and res["per_ap"]["ap1"] >= 0  # AP별 강도 노출
    assert res["motion_rssi"] is not None                        # 가장 교란된 AP의 RSSI


def test_static_has_no_motion_rssi_drive():
    d = MotionDetector(Config(motion_window_s=8.0, min_rssi_dbm=-85))
    res = _feed(d, [-40] * 30, bssid="ap1")
    assert res["per_ap"].get("ap1", 0) < 20  # 정적이면 AP 강도 낮음


def test_weak_aps_excluded_from_score():
    d = MotionDetector(Config(min_rssi_dbm=-60))  # -60 이하 가중 0
    # -80짜리 약신호가 크게 흔들려도 가중 0 → 점수 낮음
    res = _feed(d, [-80, -95] * 20)
    assert res["score"] < 20
