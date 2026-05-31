"""S04 · WebSocket 계약 적합성 테스트. docs/03 메시지 형식 ↔ 백엔드 송신 정합."""

from __future__ import annotations

from wibarrier.collect.base import Sample
from wibarrier.server import contract
from wibarrier.signal.distance import DistanceModel

DIST = DistanceModel()
AP_FIELDS = {"bssid", "ssid", "rssi", "distance_m", "distance_approx", "angle_deg", "angle_is_decorative", "motion"}


# ── angle (연출) ─────────────────────────────────────────────────────────────

def test_angle_deterministic_and_in_range():
    a1 = contract.angle_for("aa:bb:cc")
    a2 = contract.angle_for("aa:bb:cc")
    assert a1 == a2 and 0 <= a1 < 360


def test_angle_differs_by_bssid():
    assert contract.angle_for("ap-one") != contract.angle_for("ap-two")


# ── ap_entry (정직성 플래그) ─────────────────────────────────────────────────

def test_ap_entry_fields_and_flags():
    s = Sample.now("aa:bb", "Home", -52, 2437)
    e = contract.ap_entry(s, DIST)
    assert set(e) == AP_FIELDS
    assert e["distance_approx"] is True           # 거리=근사
    assert e["angle_is_decorative"] is True        # 각도=연출
    assert 0 <= e["angle_deg"] < 360
    assert e["distance_m"] == DIST.estimate(-52)
    assert isinstance(e["rssi"], int)


# ── 메시지 구조 ──────────────────────────────────────────────────────────────

def test_state_message_structure():
    samples = [Sample.now("aa", "A", -40, 2437), Sample.now("bb", "B", -70, 5180)]
    m = contract.state_message(123.0, 37, samples, DIST)
    assert m["type"] == "state"
    assert m["ts"] == 123.0 and m["motion_score"] == 37
    assert len(m["aps"]) == 2
    assert all(set(ap) == AP_FIELDS for ap in m["aps"])


def test_state_message_motion_distance_and_per_ap():
    samples = [Sample.now("aa", "A", -40, 2437)]
    m = contract.state_message(1.0, 70, samples, DIST, per_ap={"aa": 80}, motion_distance_m=2.5)
    assert m["motion_distance_m"] == 2.5
    assert m["aps"][0]["motion"] == 80
    m2 = contract.state_message(1.0, 0, samples, DIST)  # 기본값
    assert m2["motion_distance_m"] is None and m2["aps"][0]["motion"] == 0


def test_motion_event_message():
    m = contract.motion_event_message(1.0, 72, "high")
    assert m == {"type": "motion_event", "ts": 1.0, "score": 72, "level": "high"}


def test_status_message():
    ok = contract.status_message(True)
    assert ok == {"type": "status", "ok": True, "code": None, "message": None}
    err = contract.status_message(False, "PERMISSION_DENIED", "권한 필요")
    assert err["ok"] is False and err["code"] == "PERMISSION_DENIED"


# ── WS 통합 (MockCollector) ──────────────────────────────────────────────────

def test_ws_emits_valid_state(monkeypatch):
    monkeypatch.setenv("WIBARRIER_MOCK", "1")
    monkeypatch.setenv("WIBARRIER_MODE", "scan")
    from fastapi.testclient import TestClient

    from wibarrier.server.app import app

    with TestClient(app).websocket_connect("/ws") as wsconn:
        msg = wsconn.receive_json()
        assert msg["type"] == "state"
        assert isinstance(msg["motion_score"], int)
        assert len(msg["aps"]) >= 1
        ap = msg["aps"][0]
        assert set(ap) == AP_FIELDS
        assert ap["angle_is_decorative"] is True and ap["distance_approx"] is True
