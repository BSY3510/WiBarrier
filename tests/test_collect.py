"""S02 · 수집 계층 단위 테스트. CoreWLAN/하드웨어 없이 순수 로직 검증."""

from __future__ import annotations

import pytest

from wibarrier.collect import base, macos
from wibarrier.collect.base import CollectorError, Sample


# ── 테스트용 가짜 CWNetwork (덕타이핑) ──────────────────────────────────────

class FakeChannel:
    def __init__(self, n: int):
        self._n = n

    def channelNumber(self) -> int:
        return self._n


class FakeNetwork:
    def __init__(self, bssid, ssid, rssi, ch):
        self._bssid, self._ssid, self._rssi, self._ch = bssid, ssid, rssi, ch

    def bssid(self):
        return self._bssid

    def ssid(self):
        return self._ssid

    def rssiValue(self):
        return self._rssi

    def wlanChannel(self):
        return FakeChannel(self._ch)


# ── Sample ─────────────────────────────────────────────────────────────────

def test_sample_now_fields():
    s = Sample.now(bssid="aa:bb", ssid="Home", rssi=-52, freq=2437)
    assert (s.bssid, s.ssid, s.rssi, s.freq) == ("aa:bb", "Home", -52, 2437)
    assert isinstance(s.ts, float) and s.ts > 0


# ── 채널 → 주파수 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ch,freq", [(1, 2412), (6, 2437), (14, 2484), (36, 5180), (149, 5745), (0, 0), (-1, 0)])
def test_channel_to_freq(ch, freq):
    assert macos._channel_to_freq(ch) == freq


# ── 식별자 폴백 ──────────────────────────────────────────────────────────────

def test_ident_uses_bssid_when_present():
    assert macos._ident("aa:bb:cc", "Home") == "aa:bb:cc"


def test_ident_falls_back_to_ssid_when_bssid_redacted():
    assert macos._ident(None, "Home") == "ssid:Home"


def test_ident_empty_when_no_identifier():
    assert macos._ident(None, "") == ""


# ── 네트워크 파싱 ────────────────────────────────────────────────────────────

def test_sample_from_network_with_bssid():
    s = macos._sample_from_network(FakeNetwork("aa:bb", "Home", -52, 6))
    assert s is not None
    assert (s.bssid, s.ssid, s.rssi, s.freq) == ("aa:bb", "Home", -52, 2437)


def test_sample_from_network_redacted_bssid_uses_ssid():
    s = macos._sample_from_network(FakeNetwork(None, "Home", -60, 36))
    assert s is not None
    assert s.bssid == "ssid:Home" and s.freq == 5180


def test_sample_from_network_no_identifier_returns_none():
    assert macos._sample_from_network(FakeNetwork(None, "", -60, 1)) is None


def test_parse_networks_filters_unidentifiable():
    nets = [
        FakeNetwork("aa", "A", -50, 1),
        FakeNetwork(None, "", -70, 6),   # 식별 불가 → 제외
        FakeNetwork(None, "B", -80, 36),
    ]
    samples = macos._parse_networks(nets)
    assert [s.bssid for s in samples] == ["aa", "ssid:B"]


def test_bssid_redacted_detection():
    redacted = macos._parse_networks([FakeNetwork(None, "A", -50, 1), FakeNetwork(None, "B", -60, 6)])
    assert macos._bssid_redacted(redacted) is True
    mixed = macos._parse_networks([FakeNetwork("aa", "A", -50, 1), FakeNetwork(None, "B", -60, 6)])
    assert macos._bssid_redacted(mixed) is False
    assert macos._bssid_redacted([]) is False


# ── CollectorError ───────────────────────────────────────────────────────────

def test_collector_error_carries_code_and_message():
    e = CollectorError("PERMISSION_DENIED", "권한 필요")
    assert e.code == "PERMISSION_DENIED"
    assert "권한 필요" in str(e)


# ── OS 분기 ──────────────────────────────────────────────────────────────────

def test_get_collector_unsupported_os_raises(monkeypatch):
    monkeypatch.setattr(base.platform, "system", lambda: "FreeBSD")  # 미지원 OS
    with pytest.raises(CollectorError) as ei:
        base.get_collector()
    assert ei.value.code == "NO_INTERFACE"


def test_get_collector_darwin_returns_macos(monkeypatch):
    monkeypatch.setattr(base.platform, "system", lambda: "Darwin")
    assert isinstance(base.get_collector(), macos.MacOSCollector)
