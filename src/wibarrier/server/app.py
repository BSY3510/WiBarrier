"""WiBarrier 서버 — 수집 + 신호처리 + WebSocket 푸시. docs/03 계약 준수.

실행:
  PYTHONPATH=src .venv/bin/python -m uvicorn wibarrier.server.app:app --reload
환경변수:
  WIBARRIER_MOCK=1     하드웨어 없이 합성 데이터(MockCollector)
  WIBARRIER_MODE=hybrid|scan|connected   수집 모드(기본 hybrid)
    - hybrid: 연결 AP 고속 폴링(민감한 모션) + 주기적 scan(다중 AP 블립). FR-C04
    - scan: 다중 AP만(블립 풍부, 모션 둔감 — macOS 스로틀)
    - connected: 연결 AP 1개만(고속 모션, 블립 1개)
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import replace

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from ..collect.base import CollectorError, get_collector
from ..config import DEFAULT, Config
from ..signal.distance import DistanceModel
from ..signal.motion import MotionDetector
from . import contract

app = FastAPI(title="WiBarrier", version="0.1.0")


def make_collector():
    """환경에 따라 실수집기 또는 MockCollector를 반환."""
    if os.environ.get("WIBARRIER_MOCK") == "1":
        from ..collect.mock import MockCollector

        return MockCollector()
    return get_collector()


def collect_once(collector, mode: str) -> list:
    """1회 수집. connected 모드는 연결 AP 1개(없으면 빈 리스트)."""
    if mode == "connected":
        s = collector.poll_connected()
        return [s] if s else []
    return collector.scan()


def _safe_poll(collector):
    """연결 AP 폴링. 실패/미연결은 None."""
    try:
        return collector.poll_connected()
    except CollectorError:
        return None


# 마지막 성공 스캔 캐시 — macOS 스캔 레이트리밋(Resource busy)으로 스캔이 자주 실패하므로,
# 성공분을 보관해 재연결·실패 구간에도 블립을 유지한다.
_LAST_APS: list = []


def _safe_scan(collector):
    """스캔. (samples, None) 또는 (None, CollectorError). 성공 시 캐시 갱신."""
    global _LAST_APS
    try:
        s = collector.scan()
        _LAST_APS = s
        return s, None
    except CollectorError as e:
        return None, e


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _apply_config(cfg: Config, data: dict) -> None:
    """인바운드 config 메시지를 연결별 Config에 적용(클램프). docs/03 인바운드 계약. 잘못된 값 무시."""
    try:
        if "motion_threshold" in data:
            cfg.motion_threshold = float(_clamp(float(data["motion_threshold"]), 5, 95))
        if "min_rssi_dbm" in data:
            cfg.min_rssi_dbm = int(_clamp(int(data["min_rssi_dbm"]), -100, -40))
        if "hybrid_scan_interval_s" in data:
            cfg.hybrid_scan_interval_s = float(_clamp(float(data["hybrid_scan_interval_s"]), 1, 15))
        if "scan_interval_s" in data:
            cfg.scan_interval_s = float(_clamp(float(data["scan_interval_s"]), 0.5, 15))
    except (TypeError, ValueError):
        pass


def _filter_aps(aps: list, cfg: Config) -> list:
    """블립/AP목록 표시용 필터 — min_rssi_dbm 미만 AP 제외."""
    return [s for s in aps if s.rssi >= cfg.min_rssi_dbm]


def _motion_distance(res: dict, dist: DistanceModel) -> "float | None":
    """모션이 유의(score≥33)할 때 가장 교란된 AP 경로의 대략 거리. 아니면 None. (S11)"""
    if res.get("motion_rssi") is not None and res["score"] >= 33:
        return dist.estimate(res["motion_rssi"])
    return None


async def _recv_config(sock: WebSocket, cfg: Config) -> None:
    """프론트→서버 config 메시지 수신 루프. 연결 종료/오류 시 조용히 종료."""
    try:
        while True:
            data = await sock.receive_json()
            if isinstance(data, dict) and data.get("type") == "config":
                _apply_config(cfg, data)
    except Exception:
        return


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "wibarrier", "stage": "S10"}


@app.websocket("/ws")
async def ws(sock: WebSocket) -> None:
    await sock.accept()
    cfg: Config = replace(DEFAULT)  # 연결별 설정 사본(인바운드 config로 변경, 다른 연결 무관)
    mode = os.environ.get("WIBARRIER_MODE", "hybrid")
    detector = MotionDetector(cfg)  # detector는 cfg를 라이브 참조 → 임계치 변경 즉시 반영
    dist = DistanceModel(cfg.path_loss_a_dbm, cfg.path_loss_n)
    loop = asyncio.get_event_loop()
    try:
        collector = await loop.run_in_executor(None, make_collector)
    except CollectorError as e:
        await sock.send_json(contract.status_message(False, e.code, e.message))
        return

    recv = asyncio.create_task(_recv_config(sock, cfg))  # 인바운드 설정 수신(동시)
    try:
        if mode == "hybrid":
            await _run_hybrid(sock, collector, detector, dist, cfg, loop)
        else:
            await _run_simple(sock, collector, detector, dist, cfg, mode, loop)
    except (WebSocketDisconnect, RuntimeError):
        # 클라이언트 연결 종료(또는 close 후 send 시도)는 정상 종료로 처리
        pass
    finally:
        recv.cancel()


async def _run_simple(sock, collector, detector, dist, cfg, mode, loop) -> None:
    """scan 또는 connected 단일 모드 루프."""
    while True:
        try:
            samples = await loop.run_in_executor(None, collect_once, collector, mode)
        except CollectorError as e:
            await sock.send_json(contract.status_message(False, e.code, e.message))
            await asyncio.sleep(cfg.scan_interval_s)
            continue
        if not samples and mode == "connected":
            await sock.send_json(
                contract.status_message(False, "NOT_ASSOCIATED", "Wi-Fi에 연결되어 있지 않습니다.")
            )
            await asyncio.sleep(cfg.scan_interval_s)
            continue
        res = detector.update(samples)
        now = time.time()
        await sock.send_json(contract.state_message(
            now, res["score"], _filter_aps(samples, cfg), dist,
            per_ap=res["per_ap"], motion_distance_m=_motion_distance(res, dist)))
        if res["event"]:
            await sock.send_json(contract.motion_event_message(now, res["score"], res["event"]))
        await asyncio.sleep(cfg.scan_interval_s)


async def _run_hybrid(sock, collector, detector, dist, cfg, loop) -> None:
    """하이브리드(FR-C04): 연결 AP 고속 폴링(모션) + 주기적 scan(블립).

    단일 루프라 CoreWLAN 동시 호출이 없다. 연결 시 fast_interval로 폴링하며 SCAN_EVERY 틱마다
    스캔으로 블립을 갱신한다. 미연결이면 scan 케이던스로 떨어져 scan 샘플로 모션을 본다.
    """
    aps: list = list(_LAST_APS)  # 캐시된 마지막 스캔으로 즉시 블립 표시(쿨다운 대비)

    async def refresh_blips():
        """스캔으로 블립 갱신. 일시적 SCAN_FAILED(Resource busy)는 무시하고 마지막 유지.
        권한/장치 문제만 status로 알린다(상태 카드 스팸 방지)."""
        nonlocal aps
        scanned, err = await loop.run_in_executor(None, _safe_scan, collector)
        if scanned is not None:
            aps = scanned
        elif err is not None and err.code in ("PERMISSION_DENIED", "NO_INTERFACE"):
            await sock.send_json(contract.status_message(False, err.code, err.message))

    # 초기 블립 시드: macOS는 수 초에 1회만 스캔 허용(Resource busy)하므로 간격을 두고 재시도.
    # 캐시가 비어있을 때만(첫 연결) 시도하며, 성공 즉시 중단.
    if not aps:
        for _ in range(3):
            await refresh_blips()
            if aps:
                break
            await asyncio.sleep(2.0)

    tick = 0
    while True:
        scan_every = max(1, round(cfg.hybrid_scan_interval_s / cfg.fast_interval_s))  # 설정 변경 라이브 반영
        connected = await loop.run_in_executor(None, _safe_poll, collector)
        if connected is not None:
            motion_samples = [connected]
            if tick % scan_every == 0:
                await refresh_blips()
            interval = cfg.fast_interval_s
        else:
            await refresh_blips()
            motion_samples = aps
            interval = cfg.scan_interval_s

        res = detector.update(motion_samples)
        now = time.time()
        await sock.send_json(contract.state_message(
            now, res["score"], _filter_aps(aps, cfg), dist,
            per_ap=res["per_ap"], motion_distance_m=_motion_distance(res, dist)))
        if res["event"]:
            await sock.send_json(contract.motion_event_message(now, res["score"], res["event"]))
        tick += 1
        await asyncio.sleep(interval)


# 정적 렌더러(web/)를 same-origin으로 서빙 → 브라우저에서 / 접속, /ws 구독.
# 명시 라우트(/health, /ws)가 먼저 등록되므로 우선 매칭되고, 나머지를 정적으로 처리.
_WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "web")
if os.path.isdir(_WEB_DIR):
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
