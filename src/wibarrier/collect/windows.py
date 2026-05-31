"""Windows 수집 어댑터 (netsh). docs/03 계약 준수. rssi-collection 스킬.

`netsh wlan show networks mode=bssid`는 주변 AP를, `netsh wlan show interfaces`는 연결 AP를
보여준다. Signal은 0–100%라 dBm로 근사(`percent_to_dbm`), Channel은 주파수로 변환(`channel_to_freq`).

주의: netsh 출력 라벨은 OS 로케일에 따라 다를 수 있다(여기선 영문 라벨 기준). 순수 파서
(`_parse_networks`, `_parse_interface`)는 셸 없이 픽스처로 단위 테스트한다.
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional

from .base import CollectorError, Sample, channel_to_freq, percent_to_dbm

_SSID = re.compile(r"^\s*SSID\s+\d+\s*:\s*(.*)$")
_BSSID = re.compile(r"^\s*BSSID\s+\d+\s*:\s*([0-9A-Fa-f:]{17})")
_SIGNAL = re.compile(r"^\s*Signal\s*:\s*(\d+)\s*%")
_CHANNEL = re.compile(r"^\s*Channel\s*:\s*(\d+)")


def _parse_networks(text: str) -> list[dict]:
    """netsh wlan show networks mode=bssid → [{ssid,bssid,signal,channel}]."""
    entries: list[dict] = []
    cur_ssid = ""
    cur: Optional[dict] = None
    for line in text.splitlines():
        m = _SSID.match(line)
        if m:
            cur_ssid = m.group(1).strip()
            continue
        m = _BSSID.match(line)
        if m:
            cur = {"ssid": cur_ssid, "bssid": m.group(1).lower(), "signal": None, "channel": 0}
            entries.append(cur)
            continue
        if cur is not None:
            m = _SIGNAL.match(line)
            if m:
                cur["signal"] = int(m.group(1))
                continue
            m = _CHANNEL.match(line)
            if m:
                cur["channel"] = int(m.group(1))
    return entries


def _sample_from_entry(e: dict) -> Optional[Sample]:
    ssid = (e.get("ssid") or "").strip()
    bssid = (e.get("bssid") or "").strip()
    ident = bssid or (f"ssid:{ssid}" if ssid else "")
    if not ident:
        return None
    sig = e.get("signal")
    rssi = percent_to_dbm(int(sig)) if isinstance(sig, int) else 0
    return Sample.now(bssid=ident, ssid=ssid, rssi=rssi, freq=channel_to_freq(int(e.get("channel") or 0)))


def _parse_interface(text: str) -> dict:
    """netsh wlan show interfaces → 키(소문자):값 dict."""
    d: dict = {}
    for line in text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            d[k.strip().lower()] = v.strip()
    return d


def _run(args: list[str]) -> str:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=15)
    except FileNotFoundError as e:
        raise CollectorError("NO_INTERFACE", "netsh를 찾을 수 없습니다.") from e
    except subprocess.TimeoutExpired as e:
        raise CollectorError("SCAN_FAILED", "netsh 시간 초과.") from e
    return out.stdout


class WindowsCollector:
    def scan(self) -> list[Sample]:
        out = _run(["netsh", "wlan", "show", "networks", "mode=bssid"])
        samples = [s for s in (_sample_from_entry(e) for e in _parse_networks(out)) if s]
        if not samples:
            raise CollectorError("SCAN_FAILED", "가시 AP가 없습니다(WLAN 서비스·Wi-Fi 확인).")
        return samples

    def poll_connected(self) -> Optional[Sample]:
        d = _parse_interface(_run(["netsh", "wlan", "show", "interfaces"]))
        ssid = d.get("ssid", "")
        if not ssid:
            return None  # 미연결
        sig = d.get("signal", "")
        m = re.search(r"\d+", sig)
        rssi = percent_to_dbm(int(m.group())) if m else 0
        ch = d.get("channel", "")
        mc = re.search(r"\d+", ch)
        bssid = d.get("bssid", "") or f"ssid:{ssid}"
        return Sample.now(bssid=bssid, ssid=ssid, rssi=rssi,
                          freq=channel_to_freq(int(mc.group()) if mc else 0))
