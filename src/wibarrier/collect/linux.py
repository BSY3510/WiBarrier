r"""Linux 수집 어댑터 (nmcli). docs/03 계약 준수. rssi-collection 스킬.

nmcli는 root 없이 동작하고 대부분 배포판에 있다. SIGNAL은 0–100% 품질값이라 dBm로 근사한다
(`percent_to_dbm`). 더 정밀한 dBm가 필요하면 `iw dev <iface> scan`(root 필요)을 쓸 수 있다.

`nmcli -t`(terse) 출력은 값 안의 `:`를 `\:`로 이스케이프하므로, 필드 분리는 '이스케이프되지 않은
콜론'으로만 한다(BSSID·SSID가 콜론을 포함해도 안전).

순수 파서(`_nmcli_rows`, `_sample_from_row`)는 셸 없이 단위 테스트한다.
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional

from .base import CollectorError, Sample, percent_to_dbm

_UNESCAPED_COLON = re.compile(r"(?<!\\):")


def _nmcli_rows(output: str, fields: list[str]) -> list[dict]:
    """nmcli -t 출력 → 필드명 dict 리스트. 이스케이프 콜론 보존."""
    rows: list[dict] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = [p.replace("\\:", ":") for p in _UNESCAPED_COLON.split(line)]
        if len(parts) < len(fields):
            continue
        rows.append(dict(zip(fields, parts)))
    return rows


def _freq_mhz(raw: str) -> int:
    """'2412 MHz' / '5180 MHz' → 2412. 숫자만 추출."""
    m = re.search(r"\d+", raw or "")
    return int(m.group()) if m else 0


def _sample_from_row(row: dict) -> Optional[Sample]:
    bssid = (row.get("BSSID") or "").strip()
    ssid = (row.get("SSID") or "").strip()
    if bssid in ("", "--"):
        bssid = ""
    ident = bssid or (f"ssid:{ssid}" if ssid else "")
    if not ident:
        return None
    sig = (row.get("SIGNAL") or "").strip()
    rssi = percent_to_dbm(int(sig)) if sig.isdigit() else 0
    return Sample.now(bssid=ident, ssid=ssid, rssi=rssi, freq=_freq_mhz(row.get("FREQ", "")))


def _run(args: list[str]) -> str:
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=15)
    except FileNotFoundError as e:
        raise CollectorError("NO_INTERFACE", "nmcli를 찾을 수 없습니다. NetworkManager가 필요합니다.") from e
    except subprocess.TimeoutExpired as e:
        raise CollectorError("SCAN_FAILED", "nmcli 스캔 시간 초과.") from e
    if out.returncode != 0:
        raise CollectorError("SCAN_FAILED", f"nmcli 실패: {out.stderr.strip() or out.returncode}")
    return out.stdout


class LinuxCollector:
    SCAN_FIELDS = ["BSSID", "FREQ", "SIGNAL", "SSID"]
    ACTIVE_FIELDS = ["ACTIVE", "BSSID", "FREQ", "SIGNAL", "SSID"]

    def scan(self) -> list[Sample]:
        out = _run(["nmcli", "-t", "-f", ",".join(self.SCAN_FIELDS),
                    "dev", "wifi", "list", "--rescan", "auto"])
        samples = [s for s in (_sample_from_row(r) for r in _nmcli_rows(out, self.SCAN_FIELDS)) if s]
        if not samples:
            raise CollectorError("SCAN_FAILED", "가시 AP가 없습니다(Wi-Fi 활성·권한 확인).")
        return samples

    def poll_connected(self) -> Optional[Sample]:
        out = _run(["nmcli", "-t", "-f", ",".join(self.ACTIVE_FIELDS), "dev", "wifi"])
        for row in _nmcli_rows(out, self.ACTIVE_FIELDS):
            if (row.get("ACTIVE") or "").strip().lower() in ("yes", "예"):
                return _sample_from_row(row)
        return None
