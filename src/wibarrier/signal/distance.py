"""거리 추정 — 로그-거리 경로손실 모델. docs/02 FR-D01~02, PRD §8.3.

거리는 **항상 근사**다(환경·다중경로에 민감). 계약(docs/03)의 distance_approx=true로 표기되며,
프론트는 이를 정밀값처럼 표현하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_M = 0.1
MAX_M = 100.0


@dataclass
class DistanceModel:
    """RSSI(dBm) → 거리(m). d = 10^((A − RSSI) / (10·n)).

    A = 1m 기준 RSSI(dBm), n = 경로손실 지수(실내 ~2–4). 캘리브레이션으로 보정(FR-D03, 이후 단계).
    """

    a_dbm: float = -40.0
    n: float = 3.0

    def estimate(self, rssi: float) -> float:
        d = 10.0 ** ((self.a_dbm - rssi) / (10.0 * self.n))
        return round(max(MIN_M, min(d, MAX_M)), 2)

    @classmethod
    def calibrate(cls, rssis_at_1m, n: float = 3.0) -> "DistanceModel":
        """1m 거리에서 측정한 RSSI들의 중앙값을 기준 A로 삼는다(FR-D03).

        사용자가 노트북을 AP에서 1m 떨어뜨리고 측정 → A를 보정하면 거리 추정이 환경에 맞춰진다.
        표본이 없으면 기본값을 유지한다.
        """
        from statistics import median

        rssis = list(rssis_at_1m)
        if not rssis:
            return cls(n=n)
        return cls(a_dbm=float(median(rssis)), n=n)
