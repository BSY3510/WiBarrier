# 📡 WiBarrier

> 노트북 내장 WiFi가 수신하는 **주변 AP 신호(RSSI)의 변동**만으로 주변 **움직임을 감지**하고, 브라우저의 **잠수함 레이더 UI**로 시각화하는 로컬 데스크톱 서비스.

![python](https://img.shields.io/badge/python-3.9%2B-blue)
![stack](https://img.shields.io/badge/stack-FastAPI%20%C2%B7%20WebSocket%20%C2%B7%20Canvas-0E8C7A)
![local](https://img.shields.io/badge/privacy-local--only-success)
![tests](https://img.shields.io/badge/tests-66%20passing-brightgreen)

---

## ⚠️ 핵심 원칙: 정직성 (Honesty First)

이 프로젝트의 정체성은 **"측정으로 뒷받침되는 것만 사실로 표현한다"** 입니다.

| 항목 | 가능 여부 | UI 표현 |
|------|----------|---------|
| **움직임 감지** | ✅ 가능 (RSSI 변동) | 모션 스코어·이벤트·리플 |
| **거리 추정** | 🔸 거친 근사 | 거리 링·블립 반경 (`≈` 표기) |
| **방향(방위)** | ❌ **불가** (단일 라디오, 다중경로) | 각도는 **연출**(흐릿) — 실제 방위 아님을 명시 |

> 단일 WiFi 라디오로는 신호의 방향을 측정할 수 없습니다. 그래서 레이더의 **거리 링은 사실**이지만 **각도는 디자인 연출**이며, UI 하단에 *"각도는 실제 방향이 아닙니다 · 거리는 추정값입니다"* 를 상시 표기합니다. 이 한계는 [실측으로 검증](#3-방향은-왜-불가능한가--다중경로)했습니다.

---

## 개요

WiFi 신호(2.4/5GHz)는 사람의 몸(수분)에 흡수·반사됩니다. 사람이나 사물이 움직이면 **AP ↔ 노트북 경로의 신호 세기(RSSI)가 흔들립니다.** WiBarrier는 이 변동을 분석해:

1. 주변 모든 가시 AP의 RSSI를 주기적으로 수집하고,
2. 시간에 따른 변동성으로 **모션을 감지**하며,
3. 경로손실 모델로 각 AP까지의 **대략적 거리**를 추정해,
4. 브라우저에 잠수함 레이더 스타일로 **실시간 시각화**합니다.

CSI(Channel State Information) 기반 정밀 센싱과 달리 **특수 NIC·드라이버 없이 범용 노트북의 RSSI만** 사용합니다.

---

## 데모 (레이더 UI)

```
┌──────────────────────────────────────────────────────────┐
│  WiBarrier        [● 연결됨]        상태: ◉ MOTION   ☾/☀  │
├──────────────┬───────────────────────────┬───────────────┤
│ MOTION SCORE │         ◜ 레이더 ◝         │ EVENT TIMELINE│
│  ▮▮▮▮▮░ 62   │     · 거리 링(≈2/4/6m)     │  14:02 ▲ high │
│ VARIANCE ∿∿∿ │     · 블립(거리=선명,       │  13:58 ▴ low  │
│ MEASURED APs │       각도=흐릿/연출)       │ SETTINGS      │
│  iptime −63  │     · 스위프 · 모션 리플    │  민감도 ──▮   │
│  KT_GiGA −70 │     · "움직임 ≈3.4m" 링     │  최소RSSI ─▮  │
├──────────────┴───────────────────────────┴───────────────┤
│  ⚠ 각도는 실제 방향이 아닙니다 · 거리는 추정값입니다       │
└──────────────────────────────────────────────────────────┘
```

다크/라이트 테마 토글, 실시간 설정 슬라이더, 측정 중 AP 목록을 포함합니다.

---

## 작동 원리 (아키텍처)

**Python이 수집·계산의 "두뇌", 브라우저는 데이터를 받아 그리는 "교체 가능한 렌더러"** 입니다.

```
[OS별 수집 어댑터]  →  [신호 처리]      →  [FastAPI + WebSocket]  →  [브라우저 Canvas]
 macOS: CoreWLAN      모션 스코어           localhost 실시간 푸시      레이더 렌더 (dumb client)
 Linux: nmcli         거리 추정            (state/motion_event/status)
 Windows: netsh
```

- 브라우저는 보안상 RSSI를 직접 못 읽으므로, OS 권한을 가진 **로컬 Python 프로세스**가 필수입니다.
- 수집 모드: **hybrid**(연결 AP 고속 폴링으로 민감한 모션 + 주기적 스캔으로 다중 AP 블립) / scan / connected.

---

## 과학적 원리 & 공식

### 1. 거리 추정 — 로그-거리 경로손실 (Log-distance path loss)

전파는 거리에 따라 세기가 로그적으로 감소합니다. 측정 RSSI로 거리를 역산:

```
RSSI(d) = A − 10 · n · log₁₀(d)        →        d = 10^((A − RSSI) / (10 · n))
```

- `A` = 1m 기준 RSSI(dBm), `n` = 경로손실 지수(자유공간 2, 실내 ~3–4).
- 기본값 `A = −40 dBm`, `n = 3.0`. `scripts/calibrate.py`로 1m 기준값을 보정할 수 있습니다.
- **거리는 항상 근사**입니다(다중경로·차폐로 오차 ±수 m). 그래서 UI는 `≈`로 표기합니다.

### 2. 모션 감지 — RSSI 변동성 (Variability)

정적 환경의 RSSI는 안정적이고(σ ≈ 0.5 dBm), 사람이 경로를 교란하면 크게 흔들립니다. **변동성이 곧 모션 신호**입니다.

1. BSSID별 슬라이딩 윈도우(기본 8초)의 **표준편차 σ**(또는 이상치에 강건한 **MAD**) 계산
   - `σ = std(RSSI[t−w … t])`, `MAD = median(|RSSIᵢ − median(RSSI)|)`
2. **다중 AP 가중 집계** — 강신호 AP에 가중치(`w = max(0, RSSI − floor)`):
   - `agg = Σ(σᵢ · wᵢ) / Σwᵢ`
3. **0–100 정규화** — `score = clamp((agg − floor) / scale × 100, 0, 100)`
4. **EWMA 베이스라인**으로 환경 노이즈 바닥을 적응 추적(조용할 때만 갱신)
5. **히스테리시스**(상승>하강 임계치)로 이벤트 채터링 방지

> **실측 검증(M0 PoC):** 정적 σ̄ = 0.45 dBm vs 모션 σ̄ = 3.35 dBm → **변동성 비 7.49×** (몸이 AP↔노트북 경로를 가로지를 때 RSSI가 ~20 dB 급락). 다중 AP를 쓰는 이유는 각 AP가 독립 경로라, 한 경로만 교란돼도 잡히기 때문입니다.

### 3. 방향은 왜 불가능한가 — 다중경로 (Multipath)

WiFi 신호는 직접 경로 + 여러 **반사 경로**가 합쳐져 도달합니다. 단일 안테나(라디오 1개)로는 **도달각(AoA)을 측정할 수 없고**, 실내에서는 **정상파(standing wave)**가 거리·방향 신호를 압도합니다.

두 가지 방향탐지 기법을 **실측 검증**했고, 둘 다 실패했습니다:

| 방법 | 원리 | 결과 |
|------|------|------|
| 그래디언트 워크 | 4방향 이동 시 RSSI 증가 방향 = AP 방향 | ✗ — 통제 테스트서 거의 정반대(오차 144°/129°). 2m 이동의 거리 효과(~2–4dB)를 다중경로 정상파(±20dB)가 압도 |
| 그림자 스윕(occlusion) | 몸으로 가려 신호 약해지는 방위 = 도달 방향 | ✗ — 정거장 측정 시 방위 간 차이 1.5~3dB(노이즈 수준), 방향 dip 없음 |

> **핵심 비대칭:** *움직임(경로 횡단) 감지*는 되지만, *정적 방향 추정*은 안 됩니다. 그래서 레이더의 각도는 정직하게 "연출"로만 둡니다.

---

## 주요 기능

- 🛰 **실시간 레이더 UI** — 거리 링, AP 블립, 회전 스위프, 모션 리플
- 🎯 **모션 감지** — 0–100 스코어, 이벤트 타임라인, **"움직임 ≈Dm" 거리 귀속**(교란 경로의 대략 거리)
- 📏 **거리 추정** — 경로손실 모델 + 캘리브레이션 스크립트
- 🔀 **하이브리드 수집** — 고속 모션 + 다중 AP 블립 동시
- 🎚 **실시간 설정** — 모션 임계치·최소 RSSI·스캔 주기 슬라이더(WebSocket 양방향)
- 🌗 **다크/라이트 테마** — 시스템 설정 연동 + 토글
- 🖥 **크로스플랫폼 수집 계층** — macOS(검증) / Linux·Windows(어댑터 구현)
- 🔒 **로컬 전용** — 외부 전송 없음, 클라우드 미사용
- 🎭 **Mock 모드** — WiFi 없이 합성 데이터로 데모/개발

---

## 설치

```bash
git clone <repo-url> && cd WiBarrier
python3 -m venv .venv
.venv/bin/pip install -e ".[poc]"      # 코어 + macOS 수집(CoreWLAN)/분석
# 개발/테스트까지: .venv/bin/pip install -e ".[poc,dev]"
```

- 코어 의존성: `fastapi`, `uvicorn`, `websockets`
- macOS 수집(`[poc]`): `pyobjc-framework-CoreWLAN`, `numpy`, `matplotlib`
- **macOS 주의:** 전체 스캔은 시스템 설정 → 개인정보 보호 및 보안 → **위치 서비스** 권한이 필요할 수 있습니다(없으면 SSID·RSSI는 받지만 BSSID가 가려짐 — 자동 폴백).

## 사용법

```bash
# 실제 수집 (브라우저에서 http://localhost:8000)
.venv/bin/python -m uvicorn wibarrier.server.app:app --port 8000

# WiFi 없이 데모(합성 모션이 주기적으로 발생)
WIBARRIER_MOCK=1 .venv/bin/python -m uvicorn wibarrier.server.app:app --port 8000
```

| 환경변수 | 값 | 의미 |
|----------|----|------|
| `WIBARRIER_MOCK` | `1` | 합성 데이터(하드웨어 불필요) |
| `WIBARRIER_MODE` | `hybrid`(기본)/`scan`/`connected` | 수집 모드 |

**거리 캘리브레이션** — 노트북을 AP에서 ~1m 두고:
```bash
.venv/bin/python scripts/calibrate.py --seconds 10   # 권장 A값 출력 → config.py 반영
```

> 💡 **모션을 보려면**(실제 모드): 몸으로 노트북과 공유기 사이를 가로질러 움직이세요 — 그때 RSSI가 가장 크게 흔들립니다.

---

## 프로젝트 구조

```
WiBarrier/
├─ src/wibarrier/
│  ├─ collect/      OS별 수집 어댑터 (base·macos·linux·windows·mock)
│  ├─ signal/       motion(변동성 스코어) · distance(경로손실)
│  ├─ server/       app(FastAPI+WS) · contract(메시지 스키마)
│  └─ config.py
├─ web/             index.html · radar.js(Canvas) · style.css
├─ scripts/         calibrate · poc_collect/analyze · df_*(방향탐지 실험)
├─ tests/           pytest (수집·신호·계약·설정·크로스플랫폼)
└─ docs/            00-PRD(SSOT) · 03-architecture(WS 계약) · 08-design-system 등
```

자세한 설계는 [`docs/README.md`](./docs/README.md) (SSOT: [`docs/00-PRD.md`](./docs/00-PRD.md))를 참조하세요.

---

## 한계 (정직하게)

- ❌ **방향/방위 측정 불가** (단일 라디오·다중경로 — [실측 확인](#3-방향은-왜-불가능한가--다중경로))
- 🔸 **거리는 근사** (±수 m, 환경 의존)
- 🐢 **느린 샘플링** — 전체 스캔 ~0.5–1 Hz, macOS는 잦은 스캔을 `Resource busy`로 제한 → "빠른 제스처"보다 **사람의 출입·근접·통과** 감지에 적합
- 🧍 **경로 의존적** — 몸이 AP↔노트북 경로를 가로질러야 신호가 강함
- 🚫 **벽 투과 정밀 위치추적 아님** — 존재/모션 수준

---

## 개발 & 테스트

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q          # 66 tests
.venv/bin/python -m ruff check src scripts
node --check web/radar.js
```

---

## 상태

기능 v1 동작(수집 → 모션/거리 → 하이브리드 → 레이더 + 실시간 설정 + 모션 거리 표시). macOS 검증 완료, Linux/Windows 어댑터는 코드 완성·실 OS 구동 검증 대기. 진행 현황: [`docs/PROGRESS.md`](./docs/PROGRESS.md).

## 참고 / 과학적 배경

- WiFi Sensing / Device-free passive motion detection (RSSI 기반)
- Log-distance path loss model (무선 전파 거리 추정)
- 다중경로·정상파로 인한 단일 안테나 방향탐지의 한계
