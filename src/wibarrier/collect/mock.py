"""MockCollector — 하드웨어/권한 없이 합성 RSSI를 생성한다.

용도: 프론트(S06) 개발·계약 테스트를 WiFi 없이 수행. 고정 다중 AP에 작은 노이즈를 주고,
주기적으로 한 AP에 '경로 차단' 모사 급락(모션 스파이크)을 넣어 모션 경로를 검증한다.
환경변수 WIBARRIER_MOCK=1 로 서버에서 활성화한다.
"""

from __future__ import annotations

import random
from typing import Optional

from .base import Sample

_BASE_APS = [
    ("ssid:Home_2.4G", "Home_2.4G", -42, 2437),
    ("ssid:Home_5G", "Home_5G", -55, 5180),
    ("ssid:Neighbor_A", "Neighbor_A", -70, 2412),
    ("ssid:Neighbor_B", "Neighbor_B", -78, 2462),
    ("ssid:Cafe", "Cafe", -85, 5745),
]


class MockCollector:
    """합성 수집기. scan()=다중 AP, poll_connected()=가장 강한 AP."""

    def __init__(self, seed: Optional[int] = None, cycle: int = 70, burst: int = 5):
        self._rng = random.Random(seed)
        self._tick = 0
        self._cycle = cycle   # N틱 주기마다 한 번 모션 버스트(대부분은 정적)
        self._burst = burst   # 버스트 지속 틱(짧게)

    def scan(self) -> list[Sample]:
        self._tick += 1
        # 대부분 정적(작은 노이즈). 주기적으로 짧은 모션 버스트만 첫 AP에 급락(경로 차단 모사).
        in_motion = (self._tick % self._cycle) < self._burst
        out: list[Sample] = []
        for i, (bssid, ssid, base, freq) in enumerate(_BASE_APS):
            rssi = base + self._rng.randint(-1, 1)  # 평상시 작은 노이즈(정적)
            if in_motion and i == 0:
                rssi -= self._rng.randint(12, 22)   # 모션 시 큰 급락
            out.append(Sample.now(bssid=bssid, ssid=ssid, rssi=rssi, freq=freq))
        return out

    def poll_connected(self) -> Optional[Sample]:
        return self.scan()[0]
