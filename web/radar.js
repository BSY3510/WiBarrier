/* ============================================================
   WiBarrier — radar.js
   WS 클라이언트 + Canvas 레이더 렌더 + 패널 갱신.
   dumb client: 계약(docs/03)만 소비, 디자인(docs/08 §5/§6/§7/§9) 그대로.
   계약에 없는 필드 가정 금지. 제어 메시지 전송 없음(SettingsPanel disabled).
   ============================================================ */
(function () {
  "use strict";

  // ───────── 디자인 토큰 (docs/08 §2) — 테마별로 CSS 변수에서 읽어 갱신 ─────────
  // 캔버스는 CSS custom property를 자동 반영하지 못하므로, 테마 전환 시 readPalette()로 다시 읽는다.
  let C = {
    bgDeep:    "#05080F", bgEdge: "#070B14",
    primary:   "#00E5C0", primaryDim:"#0B5C50",
    warn:      "#FFB020", high:      "#FF3B47",
    textDim:   "#7A8C8A", textMute:  "#4A5A58",
  };
  let GLOW = true; // 다크=가산 발광(lighter), 라이트=평면(source-over) — docs/08 §7
  const LEVEL_COLOR = { low: C.primary, mid: C.warn, high: C.high };
  const LEVEL_RIPPLE_RINGS = { low: 1, mid: 2, high: 3 };

  function rgba(hex, a) {
    let h = String(hex).trim().replace("#", "");
    if (h.length === 3) h = h.split("").map((c) => c + c).join("");
    const n = parseInt(h, 16);
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + a + ")";
  }
  function blend() { return GLOW ? "lighter" : "source-over"; }
  function readPalette() {
    const cs = getComputedStyle(document.documentElement);
    const v = (name, fb) => (cs.getPropertyValue(name).trim() || fb);
    C = {
      bgDeep:    v("--bg-deep", "#05080F"),
      bgEdge:    v("--bg-edge", "#070B14"),
      primary:   v("--radar-primary", "#00E5C0"),
      primaryDim:v("--radar-primary-dim", "#0B5C50"),
      warn:      v("--alert-warn", "#FFB020"),
      high:      v("--alert-high", "#FF3B47"),
      textDim:   v("--text-dim", "#7A8C8A"),
      textMute:  v("--text-mute", "#4A5A58"),
    };
    GLOW = document.documentElement.dataset.theme !== "light";
    LEVEL_COLOR.low = C.primary; LEVEL_COLOR.mid = C.warn; LEVEL_COLOR.high = C.high;
  }

  // ───────── 좌표 변환 상수 (docs/08 §9) ─────────
  const MAX_RANGE_M = 6;
  const PADDING = 24; // px 외곽 여백
  const RING_METERS = [2, 4, 6];

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const HUD_FONT = '"JetBrains Mono","IBM Plex Mono",ui-monospace,monospace';

  // ───────── DOM refs ─────────
  const $ = (id) => document.getElementById(id);
  const radarCanvas   = $("radar-canvas");
  const radarCell     = $("radar-cell");
  const radarOverlay  = $("radar-overlay");
  const varCanvas     = $("variance-canvas");
  const gaugeFill     = $("gauge-fill");
  const gaugeValue    = $("gauge-value");
  const timelineEl    = $("event-timeline");
  const statusEl      = $("status-indicator");
  const connBadge     = $("conn-badge");
  const permCard      = $("permission-card");
  const permTitle     = $("perm-title");
  const permBody      = $("perm-body");
  const permRetry     = $("perm-retry");
  const settingsToggle= $("settings-toggle");
  const settingsBody  = $("settings-body");
  const themeToggle   = $("theme-toggle");
  const apList        = $("ap-list");
  const apCount       = $("ap-count");

  const ctx = radarCanvas.getContext("2d");
  const vctx = varCanvas.getContext("2d");

  // ───────── 앱 상태 ─────────
  const app = {
    radarSize: 0,            // CSS px (정사각 변)
    dpr: window.devicePixelRatio || 1,
    sweepAngle: 0,           // deg, 0 = 12시
    lastFrameTs: 0,

    aps: [],                 // 렌더용 블립 [{bssid, distance_m, angle_deg, ...anim}]
    motionScore: 0,
    varianceBuf: [],         // 최근 60 motion_score (§6.4)
    ripples: [],             // 활성 모션 리플
    events: [],              // motion_event[] (최신 상단)

    connState: "connecting", // connecting|connected|offline|denied
    radarState: "scanning",  // scanning|clear|motion|empty|disconnected
    motionUntil: 0,          // ms, motion 표시 종료 시각
    motionLevel: "low",
    motionDistanceM: null,   // 교란 경로 대략 거리(점 아님) 또는 null (S11)
    gotFirstState: false,
    statusCode: null,        // PERMISSION_DENIED|NO_INTERFACE|SCAN_FAILED|null
  };

  const VAR_MAX = 60;

  // ============================================================
  //  좌표 변환 (docs/08 §9 그대로)
  // ============================================================
  function geom() {
    const cx = app.radarSize / 2;
    const cy = app.radarSize / 2;
    const maxR = app.radarSize / 2 - PADDING;
    const pxPerM = maxR / MAX_RANGE_M;
    return { cx, cy, maxR, pxPerM };
  }
  function blipPos(distance_m, angle_deg, g) {
    const clamped = Math.min(distance_m, MAX_RANGE_M);
    const r = clamped * g.pxPerM;
    const theta = (angle_deg - 90) * Math.PI / 180; // 0°=12시
    return {
      x: g.cx + r * Math.cos(theta),
      y: g.cy + r * Math.sin(theta),
      r, theta,
      outOfRange: distance_m > MAX_RANGE_M,
    };
  }

  // 거리 표기: 항상 ≈ 접두 + 1자리 (docs/08 §5.2)
  function fmtDist(m) { return "≈" + (Math.round(m * 10) / 10).toFixed(1) + "m"; }

  // ============================================================
  //  Canvas 사이징 (docs/08 §4.2: 정사각 + dpr + ResizeObserver)
  // ============================================================
  function resizeRadar() {
    const w = radarCell.clientWidth;
    const h = radarCell.clientHeight;
    const size = Math.max(0, Math.min(w, h));
    app.radarSize = size;
    app.dpr = window.devicePixelRatio || 1;
    radarCanvas.style.width = size + "px";
    radarCanvas.style.height = size + "px";
    radarCanvas.width = Math.round(size * app.dpr);
    radarCanvas.height = Math.round(size * app.dpr);
    ctx.setTransform(app.dpr, 0, 0, app.dpr, 0, 0);
  }
  function resizeVariance() {
    const w = varCanvas.clientWidth || 248;
    const h = 80;
    const dpr = window.devicePixelRatio || 1;
    varCanvas.width = Math.round(w * dpr);
    varCanvas.height = Math.round(h * dpr);
    vctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  if (window.ResizeObserver) {
    new ResizeObserver(() => { resizeRadar(); resizeVariance(); }).observe(radarCell);
    new ResizeObserver(resizeVariance).observe(varCanvas.parentElement);
  } else {
    window.addEventListener("resize", () => { resizeRadar(); resizeVariance(); });
  }

  // ============================================================
  //  레이더 렌더 (docs/08 §6.1 레이어 순서)
  // ============================================================
  function drawRadar() {
    const size = app.radarSize;
    if (!size) return;
    const g = geom();
    const dim = app.radarState === "disconnected" || app.statusCode != null;

    ctx.clearRect(0, 0, size, size);
    ctx.save();
    if (dim) ctx.globalAlpha = 0.35;

    // 1) 배경 비네팅
    const vg = ctx.createRadialGradient(g.cx, g.cy, 0, g.cx, g.cy, g.maxR + PADDING);
    vg.addColorStop(0, C.bgDeep);
    vg.addColorStop(1, C.bgEdge);
    ctx.fillStyle = vg;
    ctx.fillRect(0, 0, size, size);

    // 2) 거리 링 (선명) + ≈ 라벨
    ctx.lineWidth = 1;
    ctx.strokeStyle = C.primaryDim;
    ctx.globalAlpha = (dim ? 0.35 : 1) * 0.6;
    ctx.font = "10px " + HUD_FONT;
    for (const m of RING_METERS) {
      const rr = m * g.pxPerM;
      ctx.beginPath();
      ctx.arc(g.cx, g.cy, rr, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.globalAlpha = dim ? 0.35 : 1;
    ctx.fillStyle = C.textDim;
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    for (const m of RING_METERS) {
      const rr = m * g.pxPerM;
      ctx.fillText(fmtDist(m), g.cx, g.cy - rr - 2); // 12시 방향 링 안쪽
    }

    // 3) 크로스헤어 (각도 눈금·방위 라벨 없음 — 정직성)
    ctx.globalAlpha = (dim ? 0.35 : 1) * 0.15;
    ctx.strokeStyle = C.primaryDim;
    ctx.beginPath();
    ctx.moveTo(g.cx - g.maxR, g.cy); ctx.lineTo(g.cx + g.maxR, g.cy);
    ctx.moveTo(g.cx, g.cy - g.maxR); ctx.lineTo(g.cx, g.cy + g.maxR);
    ctx.stroke();
    ctx.globalAlpha = dim ? 0.35 : 1;

    // 4) 스위프 (연출)
    if (!dim) drawSweep(g);

    // 5) 블립 (각 AP §5.1)
    if (!dim) for (const ap of app.aps) drawBlip(ap, g);

    // 6) 모션 리플
    if (!dim) drawRipples(g);

    // 6.5) 모션 거리 링 (S11) — 모션 시 교란된 경로의 대략 거리 강조(점 아님)
    if (!dim && app.radarState === "motion" && app.motionDistanceM != null) {
      drawMotionDistanceRing(g);
    }

    // 7) 중심 사용자 아이콘
    drawUserIcon(g);

    ctx.restore();

    // 중앙 오버레이 텍스트
    updateOverlay();
  }

  function drawSweep(g) {
    const beamDeg = 60;          // 빔 폭 (§7)
    const trailDeg = 90;         // 잔상 길이 (§7)
    const lead = (app.sweepAngle - 90) * Math.PI / 180; // 선두 (0°=12시)
    // 스위프는 레이더의 핵심 비주얼 → 항상 회전한다. reduce-motion에선 더 은은하게(낮은 alpha).
    const headAlpha = reduceMotion ? 0.20 : (GLOW ? 0.45 : 0.3);
    ctx.save();
    ctx.globalCompositeOperation = blend();
    const grad = ctx.createConicGradient
      ? ctx.createConicGradient(lead, g.cx, g.cy)
      : null;
    // conic 미지원 폴백: 단순 부채꼴
    if (grad) {
      // 선두→후미 fade: trail 구간만 채움
      grad.addColorStop(0, rgba(C.primary, headAlpha));
      grad.addColorStop(trailDeg / 360, rgba(C.primary, 0));
      grad.addColorStop(1, rgba(C.primary, 0));
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.moveTo(g.cx, g.cy);
      ctx.arc(g.cx, g.cy, g.maxR, lead, lead + Math.PI * 2);
      ctx.closePath();
      ctx.fill();
    } else {
      ctx.globalAlpha = headAlpha;
      ctx.fillStyle = C.primary;
      ctx.beginPath();
      ctx.moveTo(g.cx, g.cy);
      ctx.arc(g.cx, g.cy, g.maxR, lead - beamDeg * Math.PI / 180, lead);
      ctx.closePath();
      ctx.fill();
    }
    // 선두 edge 라인
    ctx.globalAlpha = reduceMotion ? 0.4 : 0.8;
    ctx.strokeStyle = C.primary;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(g.cx, g.cy);
    ctx.lineTo(g.cx + g.maxR * Math.cos(lead), g.cy + g.maxR * Math.sin(lead));
    ctx.stroke();
    ctx.restore();
  }

  function drawBlip(ap, g) {
    const p = blipPos(ap.distance_m, ap.angle_deg, g);
    const fade = ap.fade != null ? ap.fade : 1; // 0..1 (fade in/out)
    const baseAlpha = (p.outOfRange ? 0.4 : 1) * fade;

    // (A) 거리 호 — 사실(또렷). ±18°
    ctx.save();
    ctx.globalCompositeOperation = blend();
    const arcHalf = 18 * Math.PI / 180;
    ctx.beginPath();
    ctx.arc(g.cx, g.cy, p.r, p.theta - arcHalf, p.theta + arcHalf);
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    const hot = (ap.motion || 0) >= 40;  // S11: 모션 강한 AP 경로 강조
    ctx.strokeStyle = C.primary;
    ctx.globalAlpha = (hot ? 1.0 : 0.9) * baseAlpha;
    ctx.shadowBlur = GLOW ? (ap.pulse || hot ? 10 : 4) : 0;  // 라이트=발광 없음 (§7)
    ctx.shadowColor = C.primary;
    ctx.stroke();
    ctx.restore();

    // (B) 각도 광점 — 연출(흐림). 접선 방향 1.6배 타원
    ctx.save();
    ctx.globalCompositeOperation = blend();
    ctx.globalAlpha = baseAlpha;
    ctx.translate(p.x, p.y);
    ctx.rotate(p.theta + Math.PI / 2);          // 접선 방향으로 정렬
    ctx.scale(1.6, 1);                          // 접선 1.6배 늘림
    if (supportsCanvasFilter()) ctx.filter = "blur(3px)";
    const gR = 14;
    const rg = ctx.createRadialGradient(0, 0, 0, 0, 0, gR);
    rg.addColorStop(0,    rgba(C.primary, GLOW ? 0.55 : 0.42));
    rg.addColorStop(0.55, rgba(C.primary, GLOW ? 0.18 : 0.16));
    rg.addColorStop(1,    rgba(C.primary, 0));
    ctx.fillStyle = rg;
    ctx.beginPath();
    ctx.arc(0, 0, gR, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  function drawRipples(g) {
    const now = performance.now();
    app.ripples = app.ripples.filter((rp) => now - rp.start < (reduceMotion ? 150 : 800));
    for (const rp of app.ripples) {
      if (now < rp.start) continue;  // 아직 시작 안 한 staggered 리플 스킵(음수 반지름→ctx.arc 예외 방지)
      const t = Math.min(1, (now - rp.start) / (reduceMotion ? 150 : 800));
      ctx.save();
      ctx.globalCompositeOperation = blend();
      if (reduceMotion) {
        // 외곽 링 1회 색 플래시 (§7.2)
        ctx.globalAlpha = 0.6 * (1 - t);
        ctx.strokeStyle = rp.color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(g.cx, g.cy, g.maxR, 0, Math.PI * 2);
        ctx.stroke();
      } else {
        const radius = t * (g.maxR);
        ctx.globalAlpha = 0.6 * (1 - t);
        ctx.strokeStyle = rp.color;
        ctx.lineWidth = 2;
        if (GLOW && rp.color === C.high) ctx.shadowBlur = 12, ctx.shadowColor = C.high;
        ctx.beginPath();
        ctx.arc(g.cx, g.cy, radius, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.restore();
    }
  }

  function drawMotionDistanceRing(g) {
    const d = Math.min(app.motionDistanceM, MAX_RANGE_M);
    const r = d * g.pxPerM;
    const color = LEVEL_COLOR[app.motionLevel] || C.warn;
    ctx.save();
    ctx.globalCompositeOperation = blend();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 5]);
    ctx.globalAlpha = 0.9;
    if (GLOW) { ctx.shadowBlur = 10; ctx.shadowColor = color; }
    ctx.beginPath();
    ctx.arc(g.cx, g.cy, r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.shadowBlur = 0;
    ctx.globalAlpha = 1;
    ctx.fillStyle = color;
    ctx.font = "11px " + HUD_FONT;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    // 9시 방향 링 위에 라벨(상단 거리 링 라벨과 겹침 회피). 거리=경로 단서(≈), 점 아님.
    ctx.fillText("움직임 " + fmtDist(d), g.cx - r + 6, g.cy - 8);
    ctx.restore();
  }

  function drawUserIcon(g) {
    ctx.save();
    ctx.globalCompositeOperation = blend();
    // 정적 펄스 링 반경 6px opacity 0.3
    ctx.globalAlpha = 0.3;
    ctx.strokeStyle = C.primary;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(g.cx, g.cy, 6, 0, Math.PI * 2);
    ctx.stroke();
    // 노트북 픽토그램 8x6 + 베이스
    ctx.globalAlpha = 1;
    ctx.shadowBlur = GLOW ? 6 : 0; ctx.shadowColor = C.primary;
    ctx.fillStyle = C.primary;
    ctx.fillRect(g.cx - 4, g.cy - 3, 8, 6);     // 화면
    ctx.fillRect(g.cx - 6, g.cy + 3, 12, 1.5);  // 베이스
    ctx.restore();
  }

  function updateOverlay() {
    let text = "";
    switch (app.radarState) {
      case "scanning":     text = "SCANNING…"; break;
      case "empty":        text = "신호 없음\nAP 미검출"; break;
      case "disconnected": text = "연결 끊김\n재연결 중"; break;
      default:             text = "";
    }
    radarOverlay.textContent = text;
    // ARIA 라벨 (각도 미포함 — 정직성 §8.2)
    let aria;
    if (app.radarState === "disconnected") aria = "레이더, 연결 끊김, 재연결 중";
    else if (app.radarState === "scanning") aria = "레이더, 스캔 중";
    else {
      const motion = app.radarState === "motion" ? "움직임 감지" : "움직임 없음";
      aria = `레이더, ${app.aps.length}개 AP 감지, ${motion}`;
    }
    radarCanvas.setAttribute("aria-label", aria);
  }

  let _filterSupport = null;
  function supportsCanvasFilter() {
    if (_filterSupport != null) return _filterSupport;
    _filterSupport = typeof ctx.filter === "string";
    return _filterSupport;
  }

  // ============================================================
  //  애니메이션 루프
  // ============================================================
  function frame(ts) {
    const dt = app.lastFrameTs ? (ts - app.lastFrameTs) : 16;
    app.lastFrameTs = ts;

    // 스위프 회전: 항상 회전(레이더 핵심 비주얼). 일반 72deg/s(5s/rev),
    // reduce-motion은 45deg/s(8s/rev)로 더 부드럽게. 연결 끊김 시에만 정지.
    if (app.radarState !== "disconnected") {
      const speed = reduceMotion ? 45 : 72;
      app.sweepAngle = (app.sweepAngle + (speed * dt) / 1000) % 360;
      // 스위프 선두가 블립 각도 통과 시 pulse(글로우 깜빡임) — reduce-motion에선 생략
      if (!reduceMotion) {
        for (const ap of app.aps) {
          const diff = angleDiff(app.sweepAngle, ap.angle_deg);
          ap.pulse = diff < 6;
        }
      }
    }

    // motion 상태 만료 (§6.1: ~2s)
    if (app.radarState === "motion" && performance.now() > app.motionUntil) {
      recomputeRadarState();
    }

    drawRadar();
    requestAnimationFrame(frame);
  }
  function angleDiff(a, b) {
    let d = Math.abs(((a - b) % 360 + 360) % 360);
    return Math.min(d, 360 - d);
  }

  // ============================================================
  //  상태 머신
  // ============================================================
  function recomputeRadarState() {
    if (app.connState === "offline") { app.radarState = "disconnected"; return updateStatusIndicator(); }
    if (app.statusCode === "PERMISSION_DENIED") { app.radarState = "scanning"; return updateStatusIndicator(); }
    if (!app.gotFirstState) { app.radarState = "scanning"; return updateStatusIndicator(); }
    if (performance.now() < app.motionUntil) { app.radarState = "motion"; return updateStatusIndicator(); }
    if (app.aps.length === 0) { app.radarState = "empty"; return updateStatusIndicator(); }
    app.radarState = "clear";
    updateStatusIndicator();
  }

  function updateStatusIndicator() {
    statusEl.className = "status";
    const iconEl = statusEl.querySelector(".status__icon");
    const textEl = statusEl.querySelector(".status__text");
    if (app.connState === "offline") {
      statusEl.classList.add("status--offline");
      textEl.textContent = "OFFLINE";
      statusEl.setAttribute("aria-live", "polite");
    } else if (app.radarState === "motion") {
      statusEl.classList.add("status--motion", "level-" + app.motionLevel);
      textEl.textContent = "MOTION";
      statusEl.setAttribute("aria-live", "assertive");
    } else if (app.radarState === "scanning" || !app.gotFirstState) {
      statusEl.classList.add("status--scanning");
      textEl.textContent = "SCANNING";
      statusEl.setAttribute("aria-live", "polite");
    } else {
      statusEl.classList.add("status--clear");
      textEl.textContent = "CLEAR";
      statusEl.setAttribute("aria-live", "polite");
    }
  }

  function setConnBadge(state) {
    app.connState = state;
    connBadge.className = "badge badge--" + state;
    const textEl = connBadge.querySelector(".badge__text");
    const map = {
      connected:  "연결됨",
      connecting: "연결 중…",
      offline:    "연결 끊김",
      denied:     "권한 필요",
    };
    textEl.textContent = map[state] || state;
    connBadge.setAttribute("aria-label", "연결 상태: " + (map[state] || state));
    recomputeRadarState();
  }

  // ============================================================
  //  패널 갱신
  // ============================================================
  function setMotionScore(score) {
    const s = clamp(score, 0, 100);
    app.motionScore = s;
    const level = s >= 66 ? "high" : s >= 33 ? "mid" : "low";
    gaugeFill.style.width = s + "%";
    gaugeFill.className = "gauge__fill level-" + level;
    gaugeValue.textContent = String(Math.round(s));
    // VarianceGraph ring buffer (§6.4)
    app.varianceBuf.push(s);
    if (app.varianceBuf.length > VAR_MAX) app.varianceBuf.shift();
    drawVariance();
  }

  function drawVariance() {
    const w = varCanvas.clientWidth || 248;
    const h = 80;
    vctx.clearRect(0, 0, w, h);
    // 33·66 가이드라인 (점선)
    vctx.strokeStyle = C.textMute;
    vctx.setLineDash([3, 3]);
    vctx.lineWidth = 1;
    for (const gv of [33, 66]) {
      const y = h - (gv / 100) * h;
      vctx.beginPath(); vctx.moveTo(0, y); vctx.lineTo(w, y); vctx.stroke();
    }
    vctx.setLineDash([]);
    const buf = app.varianceBuf;
    if (buf.length < 2) return;
    const step = w / (VAR_MAX - 1);
    const x0 = w - (buf.length - 1) * step; // 우측 최신
    // area fill
    vctx.beginPath();
    vctx.moveTo(x0, h);
    buf.forEach((v, i) => vctx.lineTo(x0 + i * step, h - (v / 100) * h));
    vctx.lineTo(x0 + (buf.length - 1) * step, h);
    vctx.closePath();
    vctx.fillStyle = rgba(C.primary, GLOW ? 0.08 : 0.12);
    vctx.fill();
    // line
    vctx.beginPath();
    buf.forEach((v, i) => {
      const x = x0 + i * step, y = h - (v / 100) * h;
      i === 0 ? vctx.moveTo(x, y) : vctx.lineTo(x, y);
    });
    vctx.strokeStyle = C.primary;
    vctx.lineWidth = 1.5;
    vctx.stroke();
  }

  function addEvent(ev) {
    app.events.unshift(ev); // 최신 상단
    if (app.events.length > 100) app.events.pop();
    renderTimeline();
  }

  function renderTimeline() {
    if (app.events.length === 0) {
      timelineEl.innerHTML = '<li class="timeline__empty">기록된 모션 이벤트 없음</li>';
      return;
    }
    const SHAPE = { low: "▴", mid: "▲", high: "◆" };
    timelineEl.innerHTML = "";
    for (const ev of app.events) {
      const li = document.createElement("li");
      li.className = "timeline__row";
      const time = new Date(ev.ts * 1000).toLocaleTimeString("ko-KR", { hour12: false });
      const chip = document.createElement("span");
      chip.className = "timeline__chip level-" + ev.level;
      chip.textContent = (SHAPE[ev.level] || "·") + " " + ev.level.toUpperCase();
      const t = document.createElement("span");
      t.className = "timeline__time"; t.textContent = time;
      const sc = document.createElement("span");
      sc.className = "timeline__score"; sc.textContent = Math.round(ev.score);
      li.append(chip, t, sc);
      timelineEl.appendChild(li);
    }
  }

  function triggerRipple(level) {
    const color = LEVEL_COLOR[level] || C.primary;
    const rings = LEVEL_RIPPLE_RINGS[level] || 1;
    app.motionLevel = level;
    app.motionUntil = performance.now() + 2000; // §6.1 ~2s
    for (let i = 0; i < rings; i++) {
      app.ripples.push({ start: performance.now() + i * 120, color }); // stagger 120ms (§7)
    }
    recomputeRadarState();
  }

  // ============================================================
  //  PermissionCard (§6.9)
  // ============================================================
  const PERM_CONTENT = {
    PERMISSION_DENIED: {
      title: "위치 권한 필요",
      body: "macOS: 시스템 설정 → 개인정보 보호 → 위치 서비스에서 이 앱을 허용하세요.",
      retry: true,
    },
    NO_INTERFACE: {
      title: "WiFi 어댑터 없음",
      body: "WiFi가 켜져 있는지 확인하세요.",
      retry: false,
    },
    SCAN_FAILED: {
      title: "스캔 실패",
      body: "잠시 후 자동 재시도합니다. 반복되면 앱을 재시작하세요.",
      retry: false,
    },
    NOT_ASSOCIATED: {
      title: "WiFi 미연결",
      body: "연결 모드는 WiFi 연결이 필요합니다. 네트워크에 연결하거나 스캔 모드를 사용하세요.",
      retry: true,
    },
  };
  function showPermissionCard(code) {
    const c = PERM_CONTENT[code];
    if (!c) return hidePermissionCard();
    app.statusCode = code;
    permTitle.textContent = c.title;
    permBody.textContent = c.body;
    permRetry.hidden = !c.retry;
    permCard.hidden = false;
    recomputeRadarState();
  }
  function hidePermissionCard() {
    app.statusCode = null;
    permCard.hidden = true;
    recomputeRadarState();
  }
  permRetry.addEventListener("click", () => {
    hidePermissionCard();
    setConnBadge(ws && ws.readyState === WebSocket.OPEN ? "connected" : "connecting");
  });
  connBadge.addEventListener("click", () => {
    if (app.statusCode) showPermissionCard(app.statusCode);
  });

  // ============================================================
  //  테마 (다크 기본 / 라이트) — prefers-color-scheme 기본 + localStorage 영속
  // ============================================================
  const THEME_KEY = "wibarrier-theme";
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme; // "light" | "dark"
    try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* 사적 모드 등 무시 */ }
    readPalette();
    if (themeToggle) {
      const light = theme === "light";
      themeToggle.setAttribute("aria-pressed", String(light));
      themeToggle.textContent = light ? "☀" : "☾";
      themeToggle.setAttribute("aria-label", light ? "라이트 모드 (클릭 시 다크로)" : "다크 모드 (클릭 시 라이트로)");
    }
    drawVariance(); // 분산 그래프는 rAF 밖이라 즉시 재렌더
  }
  function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) { /* 무시 */ }
    const prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
    applyTheme(saved || (prefersLight ? "light" : "dark"));
  }
  if (themeToggle) {
    themeToggle.addEventListener("click", () => {
      applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
    });
  }

  // SettingsPanel 토글
  settingsToggle.addEventListener("click", () => {
    const open = settingsToggle.getAttribute("aria-expanded") === "true";
    settingsToggle.setAttribute("aria-expanded", String(!open));
    settingsBody.hidden = open;
  });

  // ============================================================
  //  설정 인터랙티브 (S10) — 슬라이더 → config 메시지(디바운스). docs/03 인바운드 계약.
  // ============================================================
  const setScan = $("set-scan"), setScanVal = $("set-scan-val");
  const setThr = $("set-thr"), setThrVal = $("set-thr-val");
  const setRssi = $("set-rssi"), setRssiVal = $("set-rssi-val");
  let _cfgTimer = null;
  function sendConfig(partial) {
    if (_cfgTimer) clearTimeout(_cfgTimer);
    _cfgTimer = setTimeout(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(Object.assign({ type: "config" }, partial)));
      }
    }, 150); // 드래그 중 과다 전송 방지
  }
  function wireSlider(input, valEl, fmt, toConfig) {
    if (!input) return;
    input.addEventListener("input", () => {
      const v = parseFloat(input.value);
      if (valEl) valEl.textContent = fmt(v);
      sendConfig(toConfig(v));
    });
  }
  wireSlider(setScan, setScanVal, (v) => v + "s", (v) => ({ hybrid_scan_interval_s: v }));
  wireSlider(setThr, setThrVal, (v) => String(v), (v) => ({ motion_threshold: v }));
  wireSlider(setRssi, setRssiVal, (v) => v + " dBm", (v) => ({ min_rssi_dbm: v }));

  // ============================================================
  //  메시지 처리 (계약 docs/03 — 3종만, 누락 필드 스킵)
  // ============================================================
  function handleMessage(msg) {
    if (!msg || typeof msg !== "object" || typeof msg.type !== "string") {
      console.warn("[WiBarrier] 형식 불명 프레임 스킵", msg);
      return;
    }
    switch (msg.type) {
      case "state":        return onState(msg);
      case "motion_event": return onMotionEvent(msg);
      case "status":       return onStatus(msg);
      default:
        console.warn("[WiBarrier] 알 수 없는 type 스킵:", msg.type);
    }
  }

  function onState(msg) {
    if (typeof msg.motion_score !== "number" || !Array.isArray(msg.aps)) {
      console.warn("[WiBarrier] state 필수 필드 누락 — 프레임 스킵", msg);
      return;
    }
    app.gotFirstState = true;
    // 블립 정규화: 계약 필드만 사용, 누락 AP 스킵
    const next = [];
    for (const ap of msg.aps) {
      if (!ap || typeof ap.bssid !== "string" ||
          typeof ap.distance_m !== "number" || typeof ap.angle_deg !== "number") {
        console.warn("[WiBarrier] AP 필드 누락 — 해당 블립 스킵", ap);
        continue;
      }
      next.push({
        bssid: ap.bssid,
        ssid: typeof ap.ssid === "string" ? ap.ssid : "",
        rssi: typeof ap.rssi === "number" ? ap.rssi : null,
        distance_m: ap.distance_m,
        angle_deg: ap.angle_deg,
        motion: typeof ap.motion === "number" ? ap.motion : 0,  // AP별 모션 강도 (S11)
        // distance_approx/angle_is_decorative: 방어적 확인만, 렌더는 항상 정직 기본
        fade: 1,
        pulse: false,
      });
    }
    app.aps = next;
    app.motionDistanceM = (typeof msg.motion_distance_m === "number") ? msg.motion_distance_m : null;  // S11
    setMotionScore(msg.motion_score);
    renderApList();
    recomputeRadarState();
  }

  // 측정 중인 AP 목록 (SSID·RSSI·≈거리). 각도는 연출이라 표시하지 않음(정직성 §5.2).
  function renderApList() {
    if (apCount) apCount.textContent = String(app.aps.length);
    if (!apList) return;
    if (app.aps.length === 0) {
      apList.innerHTML = '<li class="ap-list__empty">측정된 AP 없음</li>';
      return;
    }
    const sorted = app.aps.slice().sort((a, b) => (b.rssi ?? -999) - (a.rssi ?? -999));
    apList.innerHTML = "";
    for (const ap of sorted) {
      const li = document.createElement("li");
      li.className = "ap-list__row";
      const name = document.createElement("span");
      name.className = "ap-list__name";
      name.textContent = ap.ssid || "(hidden)";
      name.title = ap.ssid || ap.bssid;
      const rssi = document.createElement("span");
      rssi.className = "ap-list__rssi";
      rssi.textContent = ap.rssi != null ? ap.rssi + "dBm" : "—";
      const dist = document.createElement("span");
      dist.className = "ap-list__dist";
      dist.textContent = fmtDist(ap.distance_m);
      li.append(name, rssi, dist);
      apList.appendChild(li);
    }
  }

  function onMotionEvent(msg) {
    if (typeof msg.score !== "number" || typeof msg.level !== "string") {
      console.warn("[WiBarrier] motion_event 필드 누락 — 스킵", msg);
      return;
    }
    const ts = typeof msg.ts === "number" ? msg.ts : Date.now() / 1000;
    addEvent({ ts, score: msg.score, level: msg.level });
    triggerRipple(msg.level);
  }

  function onStatus(msg) {
    // ok=true → 정상, false → code별 PermissionCard
    if (msg.ok === false && typeof msg.code === "string") {
      if (msg.code === "PERMISSION_DENIED") setConnBadge("denied");
      else setConnBadge("offline");
      showPermissionCard(msg.code);
    } else if (msg.ok === true) {
      hidePermissionCard();
      if (ws && ws.readyState === WebSocket.OPEN) setConnBadge("connected");
    } else {
      console.warn("[WiBarrier] status 형식 확인 불가 — 스킵", msg);
    }
  }

  // ============================================================
  //  WebSocket 클라이언트 + 자동 재연결 (backoff 1→2→4→8s)
  // ============================================================
  let ws = null;
  let backoff = 1000;
  const BACKOFF_MAX = 8000;
  const useMock = /[?&]mock=1\b/.test(location.search);

  function connect() {
    if (useMock) return startMock();
    setConnBadge("connecting");
    try {
      ws = new WebSocket("ws://" + location.host + "/ws");
    } catch (e) {
      console.warn("[WiBarrier] WS 생성 실패, 재연결 예약", e);
      return scheduleReconnect();
    }
    ws.onopen = () => {
      backoff = 1000;
      if (!app.statusCode) setConnBadge("connected");
    };
    ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); }
      catch (err) { return console.warn("[WiBarrier] JSON 파싱 실패 — 스킵", err); }
      handleMessage(msg);
    };
    ws.onclose = () => {
      setConnBadge("offline");
      scheduleReconnect();
    };
    ws.onerror = () => { /* close가 뒤따름 */ };
  }
  function scheduleReconnect() {
    setConnBadge("offline");
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, BACKOFF_MAX);
  }

  // ============================================================
  //  목 데이터 피드 (백엔드 미가동 시 ?mock=1) — 계약 형식 그대로
  // ============================================================
  function startMock() {
    setConnBadge("connected");
    const mockAps = [
      { bssid: "aa:bb:cc:dd:ee:01", ssid: "Home",   angle_deg: 137, base: 3.4 },
      { bssid: "aa:bb:cc:dd:ee:02", ssid: "Office",  angle_deg: 42,  base: 5.1 },
      { bssid: "aa:bb:cc:dd:ee:03", ssid: "Guest",   angle_deg: 268, base: 1.8 },
      { bssid: "aa:bb:cc:dd:ee:04", ssid: "Far-AP",  angle_deg: 310, base: 7.2 },
    ];
    setInterval(() => {
      const aps = mockAps.map((a) => ({
        bssid: a.bssid, ssid: a.ssid, rssi: -50 - Math.random() * 30,
        distance_m: Math.max(0.5, a.base + (Math.random() - 0.5) * 0.8),
        distance_approx: true,
        angle_deg: a.angle_deg,
        angle_is_decorative: true,
      }));
      const score = clamp(20 + Math.random() * 50, 0, 100);
      handleMessage({ type: "state", ts: Date.now() / 1000, motion_score: score, aps });
    }, 1500);
    // 가끔 motion_event
    setInterval(() => {
      if (Math.random() < 0.4) {
        const r = Math.random();
        const level = r > 0.8 ? "high" : r > 0.5 ? "mid" : "low";
        const score = level === "high" ? 75 + Math.random() * 20
                    : level === "mid" ? 45 + Math.random() * 18 : 34 + Math.random() * 10;
        handleMessage({ type: "motion_event", ts: Date.now() / 1000, score, level });
      }
    }, 3000);
  }

  // ============================================================
  //  유틸 + 부트스트랩
  // ============================================================
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function boot() {
    initTheme();          // 팔레트(C/GLOW) 확정 — 첫 렌더 전에
    resizeRadar();
    resizeVariance();
    drawVariance();
    updateStatusIndicator();
    requestAnimationFrame(frame);
    connect();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
