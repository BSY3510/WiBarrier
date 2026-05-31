#!/usr/bin/env python3
"""S01 PoC 분석 — 정적 vs 모션 구간의 RSSI 변동성을 비교해 게이트를 판정한다.

여러 AP가 잡히면(scan 모드) **AP마다** 모션/정적 변동성 비를 구하고, 가장 민감한 AP를
찾는다. 모션은 특정 경로(AP)에서만 강하게 나타날 수 있으므로 "가장 좋은 AP"가 핵심이다.

입력: data/rssi_static.csv, data/rssi_motion.csv (poc_collect.py 산출)
출력: 콘솔 표 + data/poc_result.png

사용: PYTHONPATH=src .venv/bin/python scripts/poc_analyze.py
"""

from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MIN_SAMPLES = 10        # AP가 분석 대상이 되려면 두 구간 각각 최소 표본
PASS_RATIO = 1.8        # 명확 PASS 기준
WEAK_RATIO = 1.3        # 약한 신호 기준


def load(path: str):
    by = defaultdict(list)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        for row in csv.DictReader(f):
            by[row["bssid"]].append(int(row["rssi"]))
    return {b: np.array(v) for b, v in by.items()}


def win(n: int) -> int:
    return max(3, min(12, n // 4))


def rolling_std(rssi: np.ndarray, w: int) -> np.ndarray:
    if len(rssi) < w:
        return np.array([np.std(rssi)]) if len(rssi) else np.array([0.0])
    return np.array([np.std(rssi[i : i + w]) for i in range(len(rssi) - w + 1)])


def sigma(rssi: np.ndarray) -> float:
    return float(np.mean(rolling_std(rssi, win(len(rssi)))))


def main() -> int:
    static = load(os.path.join(DATA_DIR, "rssi_static.csv"))
    motion = load(os.path.join(DATA_DIR, "rssi_motion.csv"))
    if not static or not motion:
        print("⚠ data/rssi_static.csv 와 rssi_motion.csv 가 모두 필요합니다.", file=sys.stderr)
        return 2

    rows = []
    for b in set(static) & set(motion):
        s, m = static[b], motion[b]
        if len(s) < MIN_SAMPLES or len(m) < MIN_SAMPLES:
            continue
        ss, ms = sigma(s), sigma(m)
        ratio = ms / ss if ss > 0 else float("inf")
        rows.append((b, len(s), len(m), ss, ms, ratio, float(np.mean(m))))
    if not rows:
        print("⚠ 두 구간에 공통으로 충분히 잡힌 AP가 없습니다. 더 길게/scan 모드로 수집하세요.", file=sys.stderr)
        return 2

    rows.sort(key=lambda r: -r[5])  # 변동성 비 내림차순
    print("=" * 78)
    print(f"{'AP':28s} {'n_s':>4} {'n_m':>4} {'σ_static':>9} {'σ_motion':>9} {'ratio':>7} {'rssi':>6}")
    print("-" * 78)
    for b, ns, nm, ss, ms, ratio, mr in rows[:8]:
        label = (b[:27]) if len(b) > 27 else b
        print(f"{label:28s} {ns:4d} {nm:4d} {ss:9.2f} {ms:9.2f} {ratio:7.2f} {mr:6.0f}")
    print("-" * 78)

    best = rows[0]
    best_ratio = best[5]
    if best_ratio >= PASS_RATIO:
        verdict = f"PASS (best AP ratio {best_ratio:.2f}x >= {PASS_RATIO})"
    elif best_ratio >= WEAK_RATIO:
        verdict = f"WEAK (best {best_ratio:.2f}x — 경계. 기하/지속시간 개선 권장)"
    else:
        verdict = f"FAIL (best {best_ratio:.2f}x < {WEAK_RATIO} — 신호 불충분)"
    print(f"분석 AP 수: {len(rows)}  ·  최고 민감 AP: {best[0]}")
    print(f"게이트 판정: {verdict}")
    print("=" * 78)

    _plot(static, motion, best[0])
    return 0


def _plot(static, motion, bssid):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa: BLE001
        print(f"(그래프 생략: {e})")
        return
    s = static.get(bssid, np.array([]))
    m = motion[bssid]
    w = win(max(len(s), len(m)))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6))
    if len(s):
        ax1.plot(s, label=f"static (sigma={sigma(s):.2f})", color="#2c7fb8")
    ax1.plot(m, label=f"motion (sigma={sigma(m):.2f})", color="#d7301f")
    ax1.set_title(f"RSSI timeline (best AP: {bssid})")
    ax1.set_ylabel("RSSI (dBm)"); ax1.legend(); ax1.grid(alpha=.3)
    if len(s):
        ax2.plot(rolling_std(s, w), color="#2c7fb8", label="static sigma")
    ax2.plot(rolling_std(m, w), color="#d7301f", label="motion sigma")
    ratio = sigma(m) / sigma(s) if len(s) and sigma(s) > 0 else float("nan")
    ax2.set_title(f"sliding-window std (w={w})  ratio={ratio:.2f}x")
    ax2.set_ylabel("sigma (dBm)"); ax2.set_xlabel("window index"); ax2.legend(); ax2.grid(alpha=.3)
    fig.tight_layout()
    out = os.path.join(DATA_DIR, "poc_result.png")
    fig.savefig(out, dpi=110)
    print(f"그래프 저장: {out}")


if __name__ == "__main__":
    raise SystemExit(main())
