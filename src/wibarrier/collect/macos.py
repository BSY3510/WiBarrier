"""macOS 수집 어댑터 (CoreWLAN via PyObjC). docs/03 계약 준수.

제약(PRD §4.3, S01 PoC 학습):
- `airport` CLI는 제거됨(Sonoma+). CoreWLAN을 사용한다.
- 전체 스캔(scanForNetworks)은 **위치 서비스 권한**이 필요하다. 권한이 없어도 SSID·RSSI는
  취득되나 BSSID가 가려진다 → SSID를 식별자로 폴백한다(`_ident`).
- 연결 AP 1개의 RSSI(rssiValue)는 스캔 없이 읽히며 스로틀링이 없다 → 고속 폴링(모션)에 적합.
  (S01: 몸이 AP↔노트북 경로를 가로지를 때 RSSI ~20dB 급락 확인.)

순수 파싱 로직(`_sample_from_network`, `_parse_networks`, `_ident`, `_channel_to_freq`)은
CoreWLAN 없이 단위 테스트할 수 있도록 모듈 함수로 분리했다.
"""

from __future__ import annotations

from typing import Optional

from .base import CollectorError, Sample


# ── 순수 함수 (하드웨어/권한 없이 테스트 가능) ──────────────────────────────

def _channel_to_freq(ch: int) -> int:
    """채널 번호 → 대략 주파수(MHz). 단위 일관성용(정밀 불필요)."""
    if ch <= 0:
        return 0
    if ch == 14:
        return 2484
    if ch <= 14:           # 2.4GHz
        return 2407 + ch * 5
    return 5000 + ch * 5   # 5GHz


def _ident(bssid, ssid: str) -> str:
    """AP 식별자. BSSID가 있으면 사용, 없으면(위치 권한 미허용 시 가려짐) SSID로 대체.

    macOS에서 위치 서비스 권한 없이도 SSID·RSSI는 취득되나 BSSID는 가려진다.
    PoC/단일 노트북은 SSID 키로 충분하다(동일 SSID 복수 AP는 드물고, 합산은 모션 검출에 무해).
    """
    if bssid:
        return str(bssid)
    return f"ssid:{ssid}" if ssid else ""


def _sample_from_network(n) -> Optional[Sample]:
    """CoreWLAN CWNetwork(또는 동등 덕타이핑 객체) → Sample. 식별 불가 시 None."""
    ssid = str(n.ssid() or "")
    ident = _ident(n.bssid(), ssid)
    if not ident:
        return None
    ch_obj = n.wlanChannel()
    ch = int(ch_obj.channelNumber()) if ch_obj else 0
    return Sample.now(bssid=ident, ssid=ssid, rssi=int(n.rssiValue() or 0), freq=_channel_to_freq(ch))


def _parse_networks(networks) -> list[Sample]:
    """네트워크 컬렉션 → Sample 리스트(식별 불가 항목 제외)."""
    return [s for s in (_sample_from_network(n) for n in networks) if s is not None]


def _bssid_redacted(samples: list[Sample]) -> bool:
    """모든 식별자가 SSID 폴백이면 BSSID가 가려진 상태(위치 권한 부재)."""
    return bool(samples) and all(s.bssid.startswith("ssid:") for s in samples)


# ── CoreWLAN 연동 ──────────────────────────────────────────────────────────

def _interface():
    try:
        from CoreWLAN import CWWiFiClient
    except ImportError as e:  # pragma: no cover
        raise CollectorError(
            "NO_INTERFACE",
            'CoreWLAN(PyObjC) 미설치: pip install -e ".[poc]"',
        ) from e
    iface = CWWiFiClient.sharedWiFiClient().interface()
    if iface is None:
        raise CollectorError("NO_INTERFACE", "Wi-Fi 인터페이스를 찾을 수 없습니다.")
    return iface


class MacOSCollector:
    """CoreWLAN 기반 수집기. scan()=다중 AP, poll_connected()=연결 AP 1개."""

    def poll_connected(self) -> Optional[Sample]:
        iface = _interface()
        rssi = int(iface.rssiValue() or 0)
        if rssi == 0:
            return None  # 미연결 — RSSI로 판정(ssid는 권한에 따라 가려질 수 있음)
        ssid = str(iface.ssid() or "(connected)")
        ident = _ident(iface.bssid(), ssid)
        ch_obj = iface.wlanChannel()
        ch = int(ch_obj.channelNumber()) if ch_obj else 0
        return Sample.now(bssid=ident, ssid=ssid, rssi=rssi, freq=_channel_to_freq(ch))

    def scan(self) -> list[Sample]:
        iface = _interface()
        networks, err = iface.scanForNetworksWithName_error_(None, None)
        if err is not None:
            raise CollectorError("SCAN_FAILED", f"스캔 실패: {err}")
        if not networks:
            raise CollectorError(
                "PERMISSION_DENIED",
                "스캔 결과가 비었습니다. Wi-Fi가 켜져 있는지, macOS 설정 > 개인정보 보호 "
                "및 보안 > 위치 서비스에서 이 터미널(또는 Python)에 권한이 허용됐는지 확인하세요.",
            )
        samples = _parse_networks(networks)
        if not samples:
            raise CollectorError("PERMISSION_DENIED", "식별 가능한 AP가 없습니다(권한/환경 확인 필요).")
        return samples

    def diagnose(self) -> dict:
        """스모크용 상태 진단. 예외를 코드/메시지로 변환해 반환."""
        info: dict = {"connected": None, "scan_count": None, "bssid_redacted": None, "error": None}
        try:
            s = self.poll_connected()
            info["connected"] = None if s is None else {"ssid": s.ssid, "rssi": s.rssi}
        except CollectorError as e:
            info["error"] = {"code": e.code, "message": e.message}
        try:
            samples = self.scan()
            info["scan_count"] = len(samples)
            info["bssid_redacted"] = _bssid_redacted(samples)
        except CollectorError as e:
            info["scan_error"] = {"code": e.code, "message": e.message}
        return info
