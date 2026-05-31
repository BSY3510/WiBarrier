"""모션 스코어 엔진 — RSSI 시계열 변동성 → 0–100. docs/02 FR-M01~03, PRD §8.2.

원리(S01 PoC 실증): 사람이 AP↔노트북 경로를 가로지르면 RSSI가 급변한다. 정적 σ≈0.45dBm,
모션 σ≈3.35dBm. 따라서 **슬라이딩 윈도우 변동성**이 모션의 신호다.

stdlib만 사용한다(코어 런타임에 numpy 비의존 — 서버는 fastapi/uvicorn만 필요).
"""

from __future__ import annotations

from collections import defaultdict, deque
from statistics import median, pstdev
from typing import Optional

from ..collect.base import Sample
from ..config import DEFAULT, Config

# 정규화 상수 — S01 데이터 기준(정적 σ~0.5, 명확 모션 σ~4).
FLOOR_SIGMA = 0.6   # 이하이면 사실상 정적(노이즈 바닥)
SCALE_SIGMA = 3.5   # FLOOR + SCALE 이상이면 score=100


# ── 순수 함수 (테스트 가능) ─────────────────────────────────────────────────

def variability(rssis: list[int]) -> float:
    """윈도우 RSSI의 표준편차(dBm). 표본 2개 미만이면 0."""
    return float(pstdev(rssis)) if len(rssis) >= 2 else 0.0


def mad(rssis: list[int]) -> float:
    """median absolute deviation — 이상치에 강건한 변동성(보조)."""
    if not rssis:
        return 0.0
    m = median(rssis)
    return float(median([abs(x - m) for x in rssis]))


def strength_weight(mean_rssi: float, floor_dbm: float) -> float:
    """강신호 AP에 큰 가중치. floor 이하(약신호·노이즈)는 0."""
    return max(0.0, mean_rssi - floor_dbm)


def aggregate_variability(items: list[tuple[float, float]]) -> float:
    """(sigma, weight) 목록의 가중 평균. 가중 합이 0이면 0."""
    total_w = sum(w for _, w in items)
    if total_w <= 0:
        return 0.0
    return sum(s * w for s, w in items) / total_w


def to_score(agg_sigma: float, floor: float = FLOOR_SIGMA, scale: float = SCALE_SIGMA) -> float:
    """집계 변동성(dBm) → 0–100 정규화."""
    return max(0.0, min(100.0, (agg_sigma - floor) / scale * 100.0))


def level_of(score: float) -> str:
    return "high" if score >= 66 else "mid" if score >= 33 else "low"


# ── 상태 기반 검출기 ────────────────────────────────────────────────────────

class MotionDetector:
    """시간에 따라 Sample을 받아 모션 스코어·이벤트를 산출한다.

    - BSSID별 슬라이딩 윈도우(window_s) 버퍼 유지
    - 강신호 가중 다중 AP 집계 → 0–100
    - EWMA 베이스라인으로 환경 노이즈 바닥 추적(적응형 floor)
    - 임계치 히스테리시스로 이벤트 채터링 방지(FR-M03)
    """

    def __init__(self, config: Config = DEFAULT, ewma_alpha: float = 0.05):
        self.cfg = config
        self.alpha = ewma_alpha
        self.buffers: dict[str, deque] = defaultdict(deque)  # bssid -> deque[(ts, rssi)]
        self.baseline: Optional[float] = None
        self.active = False  # 모션 이벤트 진행 중 여부

    def update(self, samples: list[Sample]) -> dict:
        if samples:
            now = max(s.ts for s in samples)
            for s in samples:
                self.buffers[s.bssid].append((s.ts, s.rssi))
            self._evict(now)

        items: list[tuple[float, float]] = []
        per_ap_raw: dict[str, tuple[float, float]] = {}  # bssid -> (sigma, mean_rssi)
        for bssid, buf in self.buffers.items():
            rssis = [r for _, r in buf]
            if len(rssis) < 2:
                continue
            mean_rssi = sum(rssis) / len(rssis)
            w = strength_weight(mean_rssi, self.cfg.min_rssi_dbm)
            if w > 0:
                sig = variability(rssis)
                items.append((sig, w))
                per_ap_raw[bssid] = (sig, mean_rssi)

        agg = aggregate_variability(items)

        # 적응형 floor: 조용할 때만 베이스라인을 천천히 갱신(모션을 베이스라인이 쫓지 않게)
        if self.baseline is None:
            self.baseline = agg
        elif agg <= self.baseline + 0.5:
            self.baseline = (1 - self.alpha) * self.baseline + self.alpha * agg
        floor = max(FLOOR_SIGMA, self.baseline)

        score = to_score(agg, floor=floor)
        level = level_of(score)

        # AP별 모션 강도(0–100)와 가장 교란된 AP의 평균 RSSI(거리 귀속용)
        per_ap = {b: round(to_score(sig, floor=floor)) for b, (sig, _) in per_ap_raw.items()}
        motion_rssi = None
        if per_ap_raw:
            top_bssid = max(per_ap_raw, key=lambda b: per_ap_raw[b][0])  # σ 최대
            motion_rssi = per_ap_raw[top_bssid][1]

        event = self._event(score)
        return {
            "score": round(score),
            "level": level,
            "event": event,            # 새로 임계 돌파 시 level, 아니면 None
            "baseline": round(self.baseline, 3),
            "agg_sigma": round(agg, 3),
            "n_aps": len(items),
            "per_ap": per_ap,          # {bssid: 0–100} 모션 강도
            "motion_rssi": motion_rssi,  # 가장 교란된 AP의 평균 RSSI(거리 계산용) 또는 None
        }

    def _evict(self, now: float) -> None:
        cutoff = now - self.cfg.motion_window_s
        for bssid, buf in list(self.buffers.items()):
            while buf and buf[0][0] < cutoff:
                buf.popleft()
            if not buf:
                del self.buffers[bssid]

    def _event(self, score: float) -> Optional[str]:
        rising = self.cfg.motion_threshold
        falling = rising * 0.6
        if not self.active and score >= rising:
            self.active = True
            return level_of(score)
        if self.active and score < falling:
            self.active = False
        return None
