"""수집 공통 인터페이스 + Sample 스키마 + 에러 코드. docs/03 계약 준수.

OS별 어댑터(macos/linux/windows)는 `scan()`과 선택적으로 `poll_connected()`를
구현한다. 백엔드는 OS를 모른 채 이 인터페이스만 소비한다.
"""

from __future__ import annotations

import platform
import time
from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class Sample:
    """한 AP의 1회 측정. rssi=dBm(음수), ts=epoch seconds."""

    bssid: str
    ssid: str
    rssi: int
    freq: int
    ts: float

    @staticmethod
    def now(bssid: str, ssid: str, rssi: int, freq: int) -> "Sample":
        return Sample(bssid=bssid, ssid=ssid, rssi=rssi, freq=freq, ts=time.time())


def percent_to_dbm(percent: int) -> int:
    """신호 품질 %(Linux nmcli·Windows netsh) → dBm 근사. 0%→-100, 100%→-50.

    절대 정확치는 아니나 단조(monotonic)하므로 모션·거리 추정에 일관되게 쓸 수 있다.
    dBm 직접 취득이 가능하면(iw, CoreWLAN) 그쪽을 우선한다.
    """
    p = max(0, min(100, percent))
    return p // 2 - 100


def channel_to_freq(ch: int) -> int:
    """채널 번호 → 대략 주파수(MHz). 단위 일관성용(정밀 불필요)."""
    if ch <= 0:
        return 0
    if ch == 14:
        return 2484
    if ch <= 14:           # 2.4GHz
        return 2407 + ch * 5
    return 5000 + ch * 5   # 5GHz


class CollectorError(Exception):
    """수집 실패. code는 docs/03 계약의 status code와 일치."""

    def __init__(self, code: str, message: str):
        self.code = code          # PERMISSION_DENIED | NO_INTERFACE | NOT_ASSOCIATED | SCAN_FAILED
        self.message = message
        super().__init__(f"[{code}] {message}")


class Collector(Protocol):
    def scan(self) -> list[Sample]:
        """주변 모든 가시 AP 1회 스캔. 실패 시 CollectorError."""
        ...

    def poll_connected(self) -> Optional[Sample]:
        """보조 고속 폴링: 연결 AP 1개. 미연결이면 None."""
        ...


def get_collector() -> Collector:
    """현재 OS에 맞는 수집기를 반환한다."""
    system = platform.system()
    if system == "Darwin":
        from .macos import MacOSCollector

        return MacOSCollector()
    if system == "Linux":
        from .linux import LinuxCollector

        return LinuxCollector()
    if system == "Windows":
        from .windows import WindowsCollector

        return WindowsCollector()
    raise CollectorError("NO_INTERFACE", f"지원하지 않는 OS: {system}")
