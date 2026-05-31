#!/usr/bin/env python3
"""S01 PoC 수집 — RSSI 시계열을 라벨과 함께 CSV로 로깅한다.

목적: "사람이 움직일 때 RSSI 변동(σ)이 정적 대비 상승하는가"를 검증할 데이터 확보.

사용 (PYTHONPATH=src 필요):
  # 1) 정적: 아무도 움직이지 않는 상태로 60초
  PYTHONPATH=src .venv/bin/python scripts/poc_collect.py --label static --duration 60
  # 2) 모션: 노트북 앞 2~3m를 계속 왕복하며 60초
  PYTHONPATH=src .venv/bin/python scripts/poc_collect.py --label motion --duration 60

모드:
  --mode connected  (기본) 연결된 AP 1개를 빠르게 폴링(고시간해상도). Wi-Fi 연결 필요.
  --mode scan       주변 모든 AP 스캔(~1Hz). macOS 위치 서비스 권한 필요.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from wibarrier.collect.base import CollectorError, get_collector  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="구간 라벨 (예: static, motion)")
    ap.add_argument("--duration", type=float, default=60.0, help="수집 시간(초)")
    ap.add_argument("--mode", choices=["connected", "scan"], default="connected")
    ap.add_argument("--interval", type=float, default=None, help="폴링 간격(초). 기본: connected=0.3, scan=1.5")
    args = ap.parse_args()

    # 연속 스캔은 macOS가 스로틀링하므로 scan 모드는 간격을 넉넉히. 모션 검출엔 connected 권장.
    interval = args.interval if args.interval is not None else (0.3 if args.mode == "connected" else 4.0)
    os.makedirs(DATA_DIR, exist_ok=True)
    out_path = os.path.join(DATA_DIR, f"rssi_{args.label}.csv")

    collector = get_collector()

    # 사전 점검: 한 번 시도해 즉시 실패를 알린다(권한/미연결).
    try:
        if args.mode == "connected":
            probe = collector.poll_connected()
            if probe is None:
                print("⚠ Wi-Fi에 연결되어 있지 않습니다. 네트워크에 연결한 뒤 다시 실행하세요.", file=sys.stderr)
                print("  (또는 --mode scan 으로 주변 AP를 스캔 — 위치 서비스 권한 필요)", file=sys.stderr)
                return 2
            print(f"연결 AP: {probe.ssid}  RSSI={probe.rssi}dBm  → connected 모드로 폴링")
        else:
            n = len(collector.scan())
            print(f"스캔 {n}개 AP 감지 → scan 모드로 수집")
    except CollectorError as e:
        print(f"⚠ [{e.code}] {e.message}", file=sys.stderr)
        return 2

    n_rows = 0
    rssi_min, rssi_max = 999, -999
    t_end = time.time() + args.duration
    print(f"수집 시작: label={args.label} mode={args.mode} interval={interval}s → {out_path}")
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "label", "bssid", "ssid", "rssi", "freq"])
        try:
            while time.time() < t_end:
                try:
                    samples = (
                        [s for s in [collector.poll_connected()] if s]
                        if args.mode == "connected"
                        else collector.scan()
                    )
                except CollectorError as e:
                    print(f"  수집 일시 실패 [{e.code}] — 계속", file=sys.stderr)
                    samples = []
                for s in samples:
                    w.writerow([f"{s.ts:.3f}", args.label, s.bssid, s.ssid, s.rssi, s.freq])
                    n_rows += 1
                    rssi_min, rssi_max = min(rssi_min, s.rssi), max(rssi_max, s.rssi)
                if samples and args.mode == "connected":
                    print(f"\r  rssi={samples[0].rssi}dBm  rows={n_rows}", end="", flush=True)
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n중단됨.")
    print(f"\n완료: {n_rows} rows, RSSI [{rssi_min}, {rssi_max}] dBm → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
