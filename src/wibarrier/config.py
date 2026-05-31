"""런타임 설정. PRD §6 F5 / docs/02 FR-S01.

값은 단계가 진행되며 서버 설정 엔드포인트와 연결된다(S07).
"""

from dataclasses import dataclass


@dataclass
class Config:
    scan_interval_s: float = 1.5      # 다중 AP 스캔 주기(초). PRD §4.3 ~0.5–1Hz
    fast_interval_s: float = 0.3      # 하이브리드 연결 AP 고속 폴링 주기(초). FR-C04
    hybrid_scan_interval_s: float = 5.0  # 하이브리드 블립 갱신(스캔) 주기. macOS "Resource busy" 회피 위해 여유
    motion_window_s: float = 8.0      # 모션 변동성 슬라이딩 윈도우(초)
    motion_threshold: float = 40.0    # 모션 이벤트 임계치(0–100)
    min_rssi_dbm: int = -85           # 표시/계산에 포함할 최소 RSSI 필터
    # 거리 모델(로그-거리 경로손실). docs/02 FR-D01
    path_loss_a_dbm: float = -40.0    # 1m 기준 RSSI
    path_loss_n: float = 3.0          # 경로손실 지수(실내)


DEFAULT = Config()
