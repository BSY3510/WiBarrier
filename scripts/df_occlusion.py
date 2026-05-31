#!/usr/bin/env python3
"""방향탐지 PoC ② 그림자(occlusion) 스윕.

노트북은 고정하고, 사용자가 노트북 주위(~0.5–1m)를 **12시에서 시계방향으로 한 바퀴** 천천히
돈다. 사용자 몸이 신호 도달 경로를 막는 방위에서 연결 AP RSSI가 떨어진다 → **RSSI 최저 방위 =
신호 도달 방향**(우세 경로). S01의 "경로 차단 시 급락"을 한 바퀴로 일반화한 것.

전제: Wi-Fi 연결. 일정한 속도로 한 바퀴(기본 18초). 노트북 화면은 한 방향 고정.
사용: PYTHONPATH=src .venv/bin/python scripts/df_occlusion.py --seconds 18
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from wibarrier.collect.base import CollectorError, get_collector  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
INTERVAL = 0.2


def _clock(deg: float) -> int:
    c = round(deg / 30) % 12
    return 12 if c == 0 else c


def _smooth(vals: list[float], w: int = 5) -> list[float]:
    out = []
    for i in range(len(vals)):
        lo, hi = max(0, i - w // 2), min(len(vals), i + w // 2 + 1)
        out.append(sum(vals[lo:hi]) / (hi - lo))
    return out


def _poll_or_exit(collector) -> int:
    try:
        s = collector.poll_connected()
    except CollectorError as e:
        print(f"\n⚠ [{e.code}] {e.message}", file=sys.stderr)
        sys.exit(2)
    if s is None:
        print("\n⚠ Wi-Fi 미연결.", file=sys.stderr)
        sys.exit(2)
    return s.rssi


def _run_stations(collector, truth_deg) -> int:
    """정거장 방식: 고정 방위(12/3/6/9시)에 멈춰 측정 → 페이싱/보간 오차 없음.

    노트북은 테이블에 고정. 사용자는 안내된 시 방향 위치(노트북에서 ~0.5–1m)로 가 멈춘다.
    최저 RSSI 위치 = 신호가 가장 막힌 방위 = 도달 방위(우세 경로).
    """
    from statistics import median

    print(f"연결 AP: {collector.poll_connected().ssid}  (정거장 방식)")
    print("노트북 고정. 안내된 시 방향 위치로 가서 '측정 중'에 멈춰 계세요.\n")
    POS = [("12시", 0), ("3시", 90), ("6시", 180), ("9시", 270)]
    results = []
    for label, az in POS:
        print(f"\a▶ {label} 위치로 이동 후 정지")
        t_end = time.time() + 4.0  # 이동(측정 안 함)
        while time.time() < t_end:
            r = _poll_or_exit(collector)
            print(f"\r    이동… [{t_end - time.time():.0f}s] rssi={r}dBm  ", end="", flush=True)
            time.sleep(0.25)
        vals = []
        t_end = time.time() + 3.0  # 측정
        while time.time() < t_end:
            vals.append(_poll_or_exit(collector))
            print(f"\r    ✋ 측정 중 [{t_end - time.time():.0f}s] rssi={vals[-1]}dBm  ", end="", flush=True)
            time.sleep(0.25)
        m = float(median(vals)) if vals else 0.0
        results.append((label, az, m))
        print(f"\r  {label}: {m:.0f}dBm                         ")

    label, az, m = min(results, key=lambda r: r[2])
    hi = max(results, key=lambda r: r[2])
    dip = hi[2] - m
    print("\n" + "=" * 52)
    for lb, _, v in results:
        print(f"  {lb}: {v:.0f}dBm")
    print("-" * 52)
    valid = dip >= 4
    if not valid:
        print(f"도달 방위: 불명확 (정거장 간 차이 {dip:.1f}dB 작음)")
    else:
        print(f"도달 방위(최저 RSSI): {label}  (정거장 dip {dip:.1f}dB)")
    if truth_deg is not None:
        err = abs((az - truth_deg + 180) % 360 - 180)
        mark = "✅" if (valid and err <= 45) else ("△" if valid and err <= 90 else "✗")
        print(f"실제 AP: {_clock(truth_deg)}시  |  오차: {err:.0f}°  {mark}")
    print("주의: '신호가 오는 방향'(우세 경로)이며 반사면일 수 있음")
    print("=" * 52)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=18.0, help="한 바퀴 도는 시간(연속 스윕)")
    ap.add_argument("--truth", type=float, default=None, help="실제 AP 방향(시계, 1~12). 추정 오차 대조용")
    ap.add_argument("--stations", action="store_true",
                    help="정거장 방식: 12/3/6/9시 위치에 멈춰 측정(페이싱 오차 없음)")
    args = ap.parse_args()
    truth_deg = ((args.truth % 12) * 30) if args.truth is not None else None

    collector = get_collector()
    try:
        s0 = collector.poll_connected()
    except CollectorError as e:
        print(f"⚠ [{e.code}] {e.message}", file=sys.stderr)
        return 2
    if s0 is None:
        print("⚠ Wi-Fi 미연결 — 연결 후 다시 실행하세요.", file=sys.stderr)
        return 2

    if args.stations:
        return _run_stations(collector, truth_deg)

    print(f"연결 AP: {s0.ssid}  RSSI={s0.rssi}dBm")
    print(f"12시(노트북 화면 방향)에 서서 준비 → {args.seconds:.0f}초간 시계방향으로 천천히 한 바퀴.")
    print("체크포인트(일정 속도 유지용): 12시 → 3시 → 6시 → 9시 → 12시")
    for c in (3, 2, 1):
        print(f"\r  준비… {c}", end="", flush=True)
        time.sleep(1.0)
    print("\r  ▶ 출발! (시계방향)        ")
    print("\a", end="")

    samples: list[tuple[float, int]] = []  # (azimuth_deg, rssi)
    t0 = time.time()
    quarters = {0: "▶ 12시 시작", args.seconds * 0.25: "3시", args.seconds * 0.5: "6시", args.seconds * 0.75: "9시"}
    announced = set()
    while True:
        t = time.time() - t0
        if t >= args.seconds:
            break
        for qt, name in quarters.items():
            if t >= qt and name not in announced:
                announced.add(name)
                print(f"\n  {name}")
        try:
            s = collector.poll_connected()
        except CollectorError as e:
            print(f"\n⚠ [{e.code}] {e.message}", file=sys.stderr)
            return 2
        if s is None:
            print("\n⚠ Wi-Fi 미연결.", file=sys.stderr)
            return 2
        az = (360.0 * t / args.seconds) % 360
        samples.append((az, s.rssi))
        print(f"\r    {az:5.0f}° ({_clock(az)}시)  rssi={s.rssi}dBm  남은 {args.seconds - t:2.0f}s ",
              end="", flush=True)
        time.sleep(INTERVAL)

    if len(samples) < 8:
        print("\n표본 부족.", file=sys.stderr)
        return 2

    az = [a for a, _ in samples]
    rssi = _smooth([float(r) for _, r in samples])
    i_min = min(range(len(rssi)), key=lambda i: rssi[i])
    i_max = max(range(len(rssi)), key=lambda i: rssi[i])
    bearing = az[i_min]
    dip = rssi[i_max] - rssi[i_min]

    print("\n" + "=" * 52)
    print(f"표본 {len(samples)}개, RSSI [{min(rssi):.0f}, {max(rssi):.0f}]dBm, dip={dip:.1f}dB")
    valid = dip >= 4
    if not valid:
        print(f"도달 방위: 불명확 (dip {dip:.1f}dB 너무 작음) — 그림자 효과 약함")
    else:
        print(f"도달 방위(최저 RSSI): {bearing:.0f}° ≈ {_clock(bearing)}시 방향  (dip {dip:.1f}dB)")
    if truth_deg is not None:
        err = abs((bearing - truth_deg + 180) % 360 - 180)
        mark = "✅" if (valid and err <= 45) else ("△" if valid and err <= 90 else "✗")
        print(f"실제 AP: {_clock(truth_deg)}시  |  오차: {err:.0f}°  {mark}")
    print("주의: '신호가 오는 방향'(우세 경로)이며 반사면일 수 있음 — AP 실제 위치와 다를 수 있음")
    print("=" * 52)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        os.makedirs(DATA_DIR, exist_ok=True)
        fig = plt.figure(figsize=(6, 6))
        axp = fig.add_subplot(111, projection="polar")
        theta = np.radians(az)
        axp.set_theta_zero_location("N")
        axp.set_theta_direction(-1)  # 시계방향
        axp.plot(theta, rssi, color="#0E8C7A")
        axp.scatter([np.radians(bearing)], [rssi[i_min]], color="#FF3B47", zorder=5, label="arrival (min RSSI)")
        axp.set_title(f"Occlusion sweep — arrival ~{_clock(bearing)} o'clock (dip {dip:.1f}dB)")
        axp.legend(loc="lower right")
        out = os.path.join(DATA_DIR, "df_occlusion.png")
        fig.savefig(out, dpi=110)
        print(f"그래프 저장: {out}")
    except Exception as e:  # noqa: BLE001
        print(f"(그래프 생략: {e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
