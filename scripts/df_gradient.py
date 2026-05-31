#!/usr/bin/env python3
"""방향탐지 PoC ① 그래디언트 워크.

가이드에 따라 중앙 → 앞 → 중앙 → 뒤 → 중앙 → 좌 → 중앙 → 우로 이동하며 연결 AP RSSI를
측정한다. 각 방향에서 RSSI가 베이스라인보다 오르면 그 방향에 AP가 있을 가능성↑.
4방향 ΔRSSI를 벡터 합해 추정 방위와 신뢰도를 낸다.

진행은 **이동(측정 안 함) → "정지! 측정 중" → 중앙 복귀**로 또렷이 나뉘며, 구간 전환마다
카운트다운과 소리(터미널 벨)로 알린다. "정지! 측정 중"일 때만 그 자리에 멈춰 있으면 된다.

전제: Wi-Fi 연결(연결 AP 고속 폴링). 노트북 방향은 일정하게 유지.
사용: PYTHONPATH=src .venv/bin/python scripts/df_gradient.py
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from statistics import median

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from wibarrier.collect.base import CollectorError, get_collector  # noqa: E402

INTERVAL = 0.25
MOVE_S = 5.0    # 이동/복귀 시간(측정 안 함) — 2m 걷고 멈추기 충분히
HOLD_S = 3.0    # 정지·측정 시간
BASE_S = 4.0
# 방위 단위벡터 (x=우, y=앞), 0°=앞(12시), 시계방향
UNIT = {"forward": (0, 1), "back": (0, -1), "right": (1, 0), "left": (-1, 0)}
LABEL = {"forward": "앞으로", "back": "뒤로", "left": "왼쪽", "right": "오른쪽"}


def _phase(collector, action: str, seconds: float, measure: bool) -> list[int]:
    print("\a", end="")  # 터미널 벨(구간 전환 알림)
    vals: list[int] = []
    t_end = time.time() + seconds
    while True:
        rem = t_end - time.time()
        if rem <= 0:
            break
        try:
            s = collector.poll_connected()
        except CollectorError as e:
            print(f"\n⚠ [{e.code}] {e.message}", file=sys.stderr)
            sys.exit(2)
        if s is None:
            print("\n⚠ Wi-Fi 미연결 — 연결 후 다시 실행하세요.", file=sys.stderr)
            sys.exit(2)
        if measure:
            vals.append(s.rssi)
        tag = f"rssi={s.rssi}dBm {'· 측정 중' if measure else ''}"
        print(f"\r  {action:22s} [{rem:2.0f}s] {tag}   ", end="", flush=True)
        time.sleep(INTERVAL)
    print()
    return vals


def _clock(deg: float) -> int:
    c = round(deg / 30) % 12
    return 12 if c == 0 else c


def _ang_err(est_deg: float, truth_deg: float) -> float:
    return abs((est_deg - truth_deg + 180) % 360 - 180)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", type=float, default=None,
                    help="실제 AP 방향(시계, 1~12). 추정 오차 대조용")
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
    print(f"연결 AP: {s0.ssid}  현재 RSSI={s0.rssi}dBm")
    print("진행: '이동' 안내가 뜨면 그 방향으로 ~2m 가고, '✋ 정지! 측정 중'이 뜨면 멈춰 유지.")
    print("(노트북 방향은 일정하게 유지. 구간 전환 시 '삐' 소리)\n")
    time.sleep(1.0)

    base = _phase(collector, "✋ 중앙 정지", BASE_S, True)
    base_m = sum(base) / len(base) if base else 0.0
    base_std = (sum((v - base_m) ** 2 for v in base) / len(base)) ** 0.5 if base else 1.0

    means: dict[str, float] = {}
    keys = ("forward", "back", "left", "right")
    for i, key in enumerate(keys):
        _phase(collector, f"➡️  {LABEL[key]} 이동(끝까지 멈추지 마세요)", MOVE_S, False)
        vals = _phase(collector, "✋ 정지! 측정 중", HOLD_S, True)
        means[key] = float(median(vals)) if vals else base_m  # 중앙값(이동 잔여 드리프트에 강건)
        if i < len(keys) - 1:  # 마지막 방향 뒤 복귀는 불필요 → 생략
            _phase(collector, "↩️  중앙 복귀", MOVE_S, False)

    print("\n" + "=" * 52)
    print(f"베이스라인(중앙) RSSI = {base_m:.1f}dBm (노이즈 σ≈{base_std:.2f})")
    vx = vy = 0.0
    for key, (ux, uy) in UNIT.items():
        d = means[key] - base_m
        vx += d * ux
        vy += d * uy
        print(f"  {LABEL[key]:4s}({key:7s}) ΔRSSI = {d:+.1f}dBm")
    mag = math.hypot(vx, vy)
    bearing = (math.degrees(math.atan2(vx, vy))) % 360
    print("-" * 52)
    valid = mag >= max(1.5, 2 * base_std)
    if not valid:
        print(f"추정 방위: 불명확 (벡터 {mag:.1f}dBm ≤ 노이즈) — 그래디언트 약함")
    else:
        print(f"추정 방위: {bearing:.0f}° ≈ {_clock(bearing)}시 방향 (벡터 {mag:.1f}dBm)")
    if truth_deg is not None:
        err = _ang_err(bearing, truth_deg)
        mark = "✅" if (valid and err <= 45) else ("△" if valid and err <= 90 else "✗")
        print(f"실제 AP: {_clock(truth_deg)}시  |  오차: {err:.0f}°  {mark}")
    print("주의: 실내 다중경로로 반사 방향을 가리킬 수 있음(추정, 정밀 아님)")
    print("=" * 52)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
