"""WebSocket 계약 메시지 빌더. docs/03-architecture.md의 단일 진실 원천을 코드로 구현.

정직성 원칙(NFR4)을 메시지 구조에 박는다:
- `angle_deg`는 BSSID 해시 기반 **연출**값이며 `angle_is_decorative=True`. 실제 방위 아님.
- `distance_m`은 **근사**이며 `distance_approx=True`.
프론트는 이 플래그를 신뢰해 각도를 방위로, 거리를 정밀값으로 표현하지 않는다.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from ..collect.base import Sample
from ..signal.distance import DistanceModel


def angle_for(bssid: str) -> int:
    """연출용 고정 각도(0–359). 같은 AP는 항상 같은 위치 → 화면 안정. 실제 방향 아님."""
    h = hashlib.md5(bssid.encode("utf-8")).hexdigest()  # noqa: S324 (비암호 용도)
    return int(h, 16) % 360


def ap_entry(sample: Sample, dist: DistanceModel, motion: int = 0) -> dict:
    return {
        "bssid": sample.bssid,
        "ssid": sample.ssid,
        "rssi": sample.rssi,
        "distance_m": dist.estimate(sample.rssi),
        "distance_approx": True,
        "angle_deg": angle_for(sample.bssid),
        "angle_is_decorative": True,
        "motion": motion,  # 0–100, 이 AP 경로의 최근 모션 강도 (S11)
    }


def state_message(ts: float, motion_score: int, samples: list[Sample], dist: DistanceModel,
                  per_ap: dict | None = None, motion_distance_m: float | None = None) -> dict:
    per_ap = per_ap or {}
    return {
        "type": "state",
        "ts": ts,
        "motion_score": motion_score,
        "motion_distance_m": motion_distance_m,  # 교란 경로 대략 거리(점 아님) 또는 null (S11)
        "aps": [ap_entry(s, dist, motion=per_ap.get(s.bssid, 0)) for s in samples],
    }


def motion_event_message(ts: float, score: int, level: str) -> dict:
    return {"type": "motion_event", "ts": ts, "score": score, "level": level}


def status_message(ok: bool, code: Optional[str] = None, message: Optional[str] = None) -> dict:
    return {"type": "status", "ok": ok, "code": code, "message": message}
