"""S08 · Linux/Windows 수집 파서 단위 테스트. 실 OS 없이 픽스처로 검증.

(실제 nmcli/netsh 구동 검증은 해당 OS에서 별도 수행 — 이 Mac에선 불가.)
"""

from __future__ import annotations

from wibarrier.collect import base, linux, windows
from wibarrier.collect.base import CollectorError, channel_to_freq, percent_to_dbm


# ── 공유 헬퍼 ────────────────────────────────────────────────────────────────

def test_percent_to_dbm_monotonic_and_bounds():
    assert percent_to_dbm(100) == -50
    assert percent_to_dbm(0) == -100
    assert percent_to_dbm(80) == -60
    assert percent_to_dbm(150) == -50   # clamp
    assert percent_to_dbm(-5) == -100   # clamp
    assert percent_to_dbm(90) > percent_to_dbm(30)  # 강신호가 더 큰(덜 음수) dBm


def test_channel_to_freq_shared():
    assert channel_to_freq(6) == 2437
    assert channel_to_freq(36) == 5180
    assert channel_to_freq(0) == 0


# ── Linux (nmcli) ────────────────────────────────────────────────────────────

NMCLI_SCAN = (
    "AA\\:BB\\:CC\\:DD\\:EE\\:01:2412 MHz:80:HomeNet\n"
    "AA\\:BB\\:CC\\:DD\\:EE\\:02:5180 MHz:55:HomeNet_5G\n"
    "--:2437 MHz:30:HiddenName\n"
    "\n"  # 빈 줄 무시
)


def test_linux_parse_scan_rows_and_samples():
    rows = linux._nmcli_rows(NMCLI_SCAN, linux.LinuxCollector.SCAN_FIELDS)
    assert len(rows) == 3
    s0 = linux._sample_from_row(rows[0])
    assert s0.bssid == "AA:BB:CC:DD:EE:01"      # 이스케이프 콜론 복원
    assert s0.ssid == "HomeNet"
    assert s0.rssi == -60                        # 80% → -60dBm
    assert s0.freq == 2412
    # BSSID '--' → SSID 폴백
    s2 = linux._sample_from_row(rows[2])
    assert s2.bssid == "ssid:HiddenName"


def test_linux_active_poll_parsing():
    out = (
        "yes:AA\\:BB\\:CC\\:DD\\:EE\\:01:2412 MHz:90:HomeNet\n"
        "no:AA\\:BB\\:CC\\:DD\\:EE\\:02:5180 MHz:40:Other\n"
    )
    rows = linux._nmcli_rows(out, linux.LinuxCollector.ACTIVE_FIELDS)
    active = [r for r in rows if r["ACTIVE"] == "yes"]
    assert len(active) == 1
    s = linux._sample_from_row(active[0])
    assert s.ssid == "HomeNet" and s.rssi == -55  # 90% → -55


def test_linux_freq_mhz_parse():
    assert linux._freq_mhz("2412 MHz") == 2412
    assert linux._freq_mhz("5180 MHz") == 5180
    assert linux._freq_mhz("") == 0


# ── Windows (netsh) ──────────────────────────────────────────────────────────

NETSH_NETWORKS = """Interface name : Wi-Fi
There are 2 networks currently visible.

SSID 1 : HomeNet
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    BSSID 1                 : aa:bb:cc:dd:ee:01
         Signal             : 80%
         Radio type         : 802.11ac
         Channel            : 6
    BSSID 2                 : aa:bb:cc:dd:ee:02
         Signal             : 50%
         Channel            : 36
SSID 2 : Neighbor
    BSSID 1                 : 11:22:33:44:55:66
         Signal             : 30%
         Channel            : 11
"""


def test_windows_parse_networks():
    entries = windows._parse_networks(NETSH_NETWORKS)
    assert len(entries) == 3
    assert entries[0] == {"ssid": "HomeNet", "bssid": "aa:bb:cc:dd:ee:01", "signal": 80, "channel": 6}
    assert entries[1]["ssid"] == "HomeNet" and entries[1]["channel"] == 36  # 같은 SSID 두번째 BSSID
    assert entries[2]["ssid"] == "Neighbor"


def test_windows_samples_from_networks():
    samples = [windows._sample_from_entry(e) for e in windows._parse_networks(NETSH_NETWORKS)]
    assert samples[0].rssi == -60 and samples[0].freq == 2437   # 80%→-60, ch6
    assert samples[1].freq == 5180                              # ch36
    assert samples[2].rssi == -85                              # 30%→-85


NETSH_IFACE = """
    Name                   : Wi-Fi
    State                  : connected
    SSID                   : HomeNet
    BSSID                  : aa:bb:cc:dd:ee:01
    Signal                 : 92%
    Channel                : 6
"""


def test_windows_interface_parse():
    d = windows._parse_interface(NETSH_IFACE)
    assert d["ssid"] == "HomeNet"
    assert d["signal"] == "92%"
    assert d["channel"] == "6"


def test_windows_interface_not_connected_returns_empty_ssid():
    d = windows._parse_interface("    State                  : disconnected\n")
    assert d.get("ssid", "") == ""


# ── OS 분기 ──────────────────────────────────────────────────────────────────

def test_get_collector_linux(monkeypatch):
    monkeypatch.setattr(base.platform, "system", lambda: "Linux")
    assert isinstance(base.get_collector(), linux.LinuxCollector)


def test_get_collector_windows(monkeypatch):
    monkeypatch.setattr(base.platform, "system", lambda: "Windows")
    assert isinstance(base.get_collector(), windows.WindowsCollector)


def test_get_collector_unknown_raises(monkeypatch):
    monkeypatch.setattr(base.platform, "system", lambda: "Plan9")
    try:
        base.get_collector()
        assert False, "should raise"
    except CollectorError as e:
        assert e.code == "NO_INTERFACE"
