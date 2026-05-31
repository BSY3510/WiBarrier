"""S10 · 설정 인터랙티브(인바운드 config) 테스트."""

from __future__ import annotations

from dataclasses import replace

from wibarrier.collect.base import Sample
from wibarrier.config import DEFAULT
from wibarrier.server.app import _apply_config, _filter_aps


# ── _apply_config (클램프·검증) ──────────────────────────────────────────────

def test_apply_config_clamps():
    cfg = replace(DEFAULT)
    _apply_config(cfg, {"motion_threshold": 200})
    assert cfg.motion_threshold == 95          # 상한 클램프
    _apply_config(cfg, {"motion_threshold": 1})
    assert cfg.motion_threshold == 5           # 하한 클램프
    _apply_config(cfg, {"min_rssi_dbm": -10})
    assert cfg.min_rssi_dbm == -40
    _apply_config(cfg, {"hybrid_scan_interval_s": 99})
    assert cfg.hybrid_scan_interval_s == 15


def test_apply_config_ignores_bad_and_unknown():
    cfg = replace(DEFAULT)
    before = cfg.motion_threshold
    _apply_config(cfg, {"motion_threshold": "bad"})  # 잘못된 타입 무시
    assert cfg.motion_threshold == before
    _apply_config(cfg, {"unknown_field": 123})        # 알 수 없는 필드 무시(에러 없음)


def test_apply_config_partial_update():
    cfg = replace(DEFAULT)
    _apply_config(cfg, {"min_rssi_dbm": -70})
    assert cfg.min_rssi_dbm == -70
    assert cfg.motion_threshold == DEFAULT.motion_threshold  # 안 보낸 필드는 그대로


# ── _filter_aps ──────────────────────────────────────────────────────────────

def test_filter_aps_by_min_rssi():
    cfg = replace(DEFAULT)
    cfg.min_rssi_dbm = -60
    aps = [Sample.now("a", "A", -42, 2437), Sample.now("b", "B", -70, 2412),
           Sample.now("c", "C", -55, 5180)]
    out = _filter_aps(aps, cfg)
    assert sorted(s.rssi for s in out) == [-55, -42]  # -70 제외


# ── WS 통합: config 전송 → 블립 필터 반영 ────────────────────────────────────

def test_ws_config_filter_reduces_blips(monkeypatch):
    monkeypatch.setenv("WIBARRIER_MOCK", "1")
    monkeypatch.setenv("WIBARRIER_MODE", "hybrid")
    from fastapi.testclient import TestClient

    from wibarrier.server.app import app

    with TestClient(app).websocket_connect("/ws") as ws:
        pre = 0
        for _ in range(8):
            m = ws.receive_json()
            if m["type"] == "state":
                pre = max(pre, len(m["aps"]))
        ws.send_json({"type": "config", "min_rssi_dbm": -50})  # 강신호만 남김
        post = 0
        for _ in range(18):
            m = ws.receive_json()
            if m["type"] == "state":
                post = max(post, len(m["aps"]))
    assert pre >= 2          # 필터 전 다중 AP
    assert post < pre        # 필터 후 블립 감소(설정이 라이브 반영됨)
