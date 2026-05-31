#!/usr/bin/env python3
"""거리 캘리브레이션 (FR-D03) — 1m 기준 RSSI(A) 측정.

노트북을 대상 AP에서 **약 1m** 떨어뜨린 뒤 실행한다. 연결 AP RSSI를 몇 초간 수집해
중앙값을 권장 A(`path_loss_a_dbm`)로 출력한다. 그 값을 config의 기본값에 반영하면
거리 추정이 환경에 맞춰진다.

사용:
  PYTHONPATH=src .venv/bin/python scripts/calibrate.py --seconds 10
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from wibarrier.collect.base import CollectorError, get_collector  # noqa: E402
from wibarrier.signal.distance import DistanceModel  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--interval", type=float, default=0.3)
    ap.add_argument("--n", type=float, default=3.0, help="경로손실 지수")
    args = ap.parse_args()

    collector = get_collector()
    rssis: list[int] = []
    print(f"AP에서 ~1m 거리 유지. {args.seconds}s 측정 시작…")
    t_end = time.time() + args.seconds
    try:
        while time.time() < t_end:
            try:
                s = collector.poll_connected()
            except CollectorError as e:
                print(f"⚠ [{e.code}] {e.message}", file=sys.stderr)
                return 2
            if s is None:
                print("⚠ Wi-Fi 미연결 — 연결 후 다시 실행하세요.", file=sys.stderr)
                return 2
            rssis.append(s.rssi)
            print(f"\r  rssi={s.rssi}dBm  n={len(rssis)}", end="", flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass

    if not rssis:
        print("\n표본 없음.", file=sys.stderr)
        return 2
    model = DistanceModel.calibrate(rssis, n=args.n)
    print(f"\n표본 {len(rssis)}개, 권장 A(1m 기준 RSSI) = {model.a_dbm:.0f} dBm, n = {model.n}")
    print(f"→ config.py 의 path_loss_a_dbm 를 {model.a_dbm:.0f} 으로 설정하면 거리 추정이 보정됩니다.")
    print(f"   검산: 이 모델에서 RSSI={model.a_dbm:.0f}dBm → {model.estimate(model.a_dbm)}m (≈1m)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
