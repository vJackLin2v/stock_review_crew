import React, { useEffect, useRef, useState } from "react";
import * as echarts from "echarts";
import { getZTTracking, runZTTracking, getZTJob, postZTTag, getStockDetail } from "../api";
import { normalizeStockDetail } from "../normalize";

/* 涨停复盘工作台（新 Tab，= docs/zt_tracking_design.html v0.2 单页卡片版） */

const WEATHER_ICON = { 晴: "☀️", 晴间多云: "⛅", 多云: "☁️", 偏冷有雨: "🌧️", 数据缺失: "⚠️" };
const SEAT_TYPE = { youzi: "游资", jigou: "机构", lianghua: "量化", waizi: "外资", unknown: "未知" };
const SEAT_COLOR = { youzi: "#E8590C", jigou: "#1971C2", lianghua: "#9C36B5", waizi: "#0CA678", unknown: "#868E96" };

const ZT_CSS = [
  ".zt-wrap{max-width:1200px;}",
  ".zt-topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;}",
  ".zt-title{font-size:13px;color:#8A8F98;}",
  ".zt-toolbar{display:flex;align-items:center;gap:8px;}",
  ".zt-date-input{border:1px solid #E6E4DF;border-radius:8px;padding:6px 8px;font-size:13px;color:#20242B;background:#fff;}",
  ".zt-date{font-size:18px;font-weight:700;color:#20242B;margin-right:8px;}",
  ".zt-error{color:#E03131;margin-bottom:8px;}",
  ".zt-card{margin-bottom:16px;}",
  ".zt-ladder-row{display:flex;align-items:center;gap:10px;margin-bottom:8px;}",
  ".zt-ladder-lbl{width:52px;font-size:12px;color:#8A8F98;}",
  ".zt-ladder-bar{flex:1;height:18px;background:#F1F0EC;border-radius:5px;overflow:hidden;}",
  ".zt-ladder-bar i{display:block;height:100%;background:linear-gradient(90deg,#FA6B69,#E03131);border-radius:5px;}",
  ".zt-ladder-num{width:36px;text-align:right;font-weight:700;color:#E03131;}",
  ".zt-legend{font-size:12px;color:#8A8F98;margin-top:10px;}",
  ".zt-topic-table .zt-topic-group{margin-bottom:14px;}",
  ".zt-topic-group-head{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 10px;background:#FAFAF8;border:1px solid #E6E4DF;border-radius:8px 8px 0 0;}",
  ".zt-caret{color:#8A8F98;font-size:12px;}",
  ".zt-collapsed{padding:8px 10px;border:1px solid #E6E4DF;border-top:none;border-radius:0 0 8px 8px;color:#8A8F98;font-size:12px;background:#FDFDFB;}",
  ".zt-topic-group-head.zt-warn-head{background:#FFF3E8;border-color:#F1D9B8;}",
  ".zt-board-name{font-size:14px;font-weight:700;color:#20242B;margin-right:4px;}",
  ".zt-pill-warn{background:#FDECEC;color:#E03131;border-color:#F3C2C2;}",
  ".zt-warn{color:#F08C00;}",
  ".zt-topics{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;}",
  ".zt-topic{background:#FFFFFF;border:1px solid #E6E4DF;border-top:3px solid #10A37F;border-radius:12px;padding:12px 14px;}",
  ".zt-topic.zt-focus{border-top-color:#E03131;box-shadow:0 2px 10px rgba(224,49,49,.08);}",
  ".zt-topic-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px;}",
  ".zt-topic-head h4{margin:0;font-size:15px;}",
  ".zt-topic-meta{font-size:12px;color:#8A8F98;}",
  ".zt-topic-meta b{color:#20242B;}",
  ".zt-chip{font-size:11px;padding:2px 8px;border-radius:6px;background:#F1F0EC;color:#555;display:inline-block;}",
  ".zt-chip-seat{background:#FDECEC;color:#E03131;}",
  ".zt-chip-row{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}",
  ".zt-pill{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid #E6E4DF;color:#8A8F98;background:#fff;}",
  ".zt-pill-focus{background:#FDECEC;color:#E03131;border-color:#F3C2C2;}",
  ".zt-pill-rot{background:#FFF3E8;color:#B25E09;border-color:#F1D9B8;}",
  ".zt-table{width:100%;border-collapse:collapse;border:1px solid #E6E4DF;border-radius:12px;overflow:hidden;}",
  ".zt-table th,.zt-table td{padding:9px 10px;text-align:left;border-bottom:1px solid #F0EFEA;font-size:13px;}",
  ".zt-table th{background:#FAFAF8;color:#8A8F98;font-weight:600;font-size:12px;}",
  ".zt-table tr:last-child td{border-bottom:none;}",
  ".zt-up{color:#E03131;font-weight:700;}",
  ".zt-muted{color:#8A8F98;font-size:12px;}",
  ".zt-core-cols{display:grid;grid-template-columns:1fr 1fr;gap:14px;}",
  "@media (max-width:900px){.zt-core-cols{grid-template-columns:1fr;}}",
  ".zt-core{background:#FFFFFF;border:1px solid #E6E4DF;border-left:4px solid #10A37F;border-radius:10px;padding:12px 14px;margin-bottom:10px;}",
  ".zt-core.zt-core-shibie{border-left-color:#E03131;}",
  ".zt-core-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px;}",
  ".zt-core-name{font-weight:700;font-size:15px;}",
  ".zt-core-code{color:#8A8F98;font-size:12px;}",
  ".zt-tags{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px;}",
  ".zt-core-info{font-size:12px;color:#8A8F98;display:flex;gap:12px;}",
  ".zt-sig{display:inline-block;font-size:12px;padding:3px 10px;border-radius:999px;margin-top:8px;}",
  ".zt-sig-buy{background:#E7FBF3;color:#087F5B;}",
  ".zt-sig-chase{background:#FDECEC;color:#E03131;}",
  ".zt-sig-wait{background:#F1F0EC;color:#6B7280;}",
  ".zt-seat{cursor:pointer;}",
  ".zt-seat:hover{box-shadow:0 0 0 2px rgba(16,163,127,.25);}",
  ".zt-foot{margin-top:10px;color:#8A8F98;font-size:12px;}",
  ".zt-modal{position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;z-index:99;}",
  ".zt-modal-box{background:#fff;border-radius:12px;padding:20px 24px;min-width:300px;max-width:520px;}",
  ".zt-modal-title{font-size:15px;font-weight:700;margin-bottom:8px;}",
  ".zt-modal-seat{font-size:13px;color:#555;margin-bottom:12px;word-break:break-all;}",
  ".zt-wide{max-width:780px;}",
  ".zt-news .zt-news-item{border:1px solid #F0EFEA;border-radius:10px;padding:10px 12px;margin-bottom:10px;background:#FDFDFB;}",
  ".zt-news-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px;}",
  ".zt-news-time{color:#8A8F98;font-size:12px;}",
  ".zt-news-src{color:#B7BCC4;font-size:11px;}",
  ".zt-news-dir{font-size:12px;padding:1px 8px;border-radius:999px;font-weight:700;}",
  ".zt-news-good{background:#FDECEC;color:#E03131;}",
  ".zt-news-bad{background:#E7FBF3;color:#0CA678;}",
  ".zt-news-neu{background:#F1F0EC;color:#6B7280;}",
  ".zt-news-text{font-size:13px;color:#20242B;line-height:1.6;margin-bottom:6px;}",
  ".zt-news-meta{font-size:12px;color:#555;display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:4px;}",
  ".zt-news-ev{font-size:12px;color:#8A8F98;border-top:1px dashed #EFEDE8;margin-top:6px;padding-top:6px;}",
  ".zt-click{cursor:pointer;}",
  ".zt-board-pool .zt-pool-summary{display:flex;flex-wrap:wrap;gap:10px;font-size:13px;color:#555;margin-bottom:12px;}",
  ".zt-board-pool .zt-pool-summary b{color:#20242B;}",
  ".zt-pool-section{font-size:13px;font-weight:700;color:#20242B;margin:10px 0 6px;}",
  ".zt-pool-codes{display:flex;flex-wrap:wrap;gap:6px;}",
  ".zt-pool-stk{cursor:pointer;}",
  ".zt-pool-stk.zt-up{background:#FDECEC;color:#E03131;}",
  ".zt-pool-stk.zt-down{background:#E7FBF3;color:#0CA678;}",
  ".zt-stock-detail .zt-stock-row{display:flex;gap:10px;padding:5px 0;font-size:13px;border-bottom:1px solid #F0EFEA;}",
  ".zt-stock-detail .zt-stock-row span{width:70px;color:#8A8F98;flex-shrink:0;}",
  ".zt-stock-detail .zt-stock-row b{color:#20242B;}",
  ".zt-stock-detail .zt-note b{color:#555;font-weight:400;line-height:1.6;}",
  ".zt-label-block{border-top:1px dashed #EFEDE8;margin-top:10px;padding-top:8px;}",
  ".zt-label-row{display:flex;gap:8px;padding:4px 0;font-size:12.5px;border-bottom:1px solid #F6F5F2;align-items:baseline;}",
  ".zt-label-row span{width:40px;color:#8A8F98;flex-shrink:0;}",
  ".zt-label-row b{font-weight:600;}",
  ".zt-label-row em{font-style:normal;}",
  ".zt-role-core{color:#E03131;}",
  ".zt-role-host{color:#1971C2;}",
  ".zt-role-weak{color:#868E96;}",
  ".zt-act-buy{color:#087F5B;}",
  ".zt-act-chase{color:#E03131;}",
  ".zt-act-avoid{color:#868E96;}",
  ".zt-act-wait{color:#F08C00;}",
  ".zt-kline-title{font-size:13px;font-weight:700;color:#20242B;margin:14px 0 4px;display:flex;align-items:center;gap:8px;}",
  ".zt-kline-box{max-width:1120px;width:94vw;}",
  ".zt-kline-head{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:8px;font-size:14px;font-weight:700;color:#20242B;}"
].join("\n");

function scoreColor(s) {
  if (s >= 80) return "#e03131";
  if (s >= 65) return "#f08c00";
  if (s >= 45) return "#fab005";
  if (s >= 30) return "#339af0";
  return "#1c7ed6";
}
function pct(v) {
  if (v == null || !isFinite(v)) return "—";
  return (v * 100).toFixed(1) + "%";
}
function amt(v) {
  if (v == null || !isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 100000000) return (v / 100000000).toFixed(2) + "亿";
  if (a >= 10000) return (v / 10000).toFixed(1) + "万";
  return v.toFixed(0);
}
function sealTime(v) {
  if (!v) return "—";
  const s = String(v).replace(/:/g, "");
  if (s.length === 6) return s.slice(0, 2) + ":" + s.slice(2, 4);
  return s;
}
function nOrDash(v) { return v != null ? v : "—"; }

/* 个股日K（复用项目 getStockDetail + ECharts candlestick + MA5/10/20；支持放大） */
function ZTKlineChart({ code, height = 320 }) {
  const ref = useRef(null);
  useEffect(() => {
    let inst = null;
    let cancelled = false;
    const raf = requestAnimationFrame(async () => {
      try {
        if (!ref.current) return;
        const raw = await getStockDetail(code, 60);
        if (cancelled || !ref.current) return;
        const d = normalizeStockDetail(raw);
        const bars = d.daily || [];
        if (!bars.length) return;
        inst = echarts.init(ref.current);
        const xs = bars.map((b) => String(b.date || "").slice(5));
        const candles = bars.map((b) => [b.open, b.close, b.low, b.high]);
        const series = [{
          name: "K线", type: "candlestick", data: candles,
          itemStyle: { color: "#E03131", color0: "#0CA678", borderColor: "#E03131", borderColor0: "#0CA678" },
        }];
        const closes = bars.map((b) => b.close);
        const maCalc = (n) => closes.map((_, i) => {
          if (i < n - 1) return null;
          const seg = closes.slice(i - n + 1, i + 1).filter((x) => x != null);
          return seg.length === n ? Number((seg.reduce((a, b) => a + b, 0) / n).toFixed(4)) : null;
        });
        const maSer = (name, color, data) => ({
          name, type: "line", data, smooth: true, showSymbol: false,
          lineStyle: { width: 1, color }, itemStyle: { color },
        });
        series.push(maSer("MA5", "#f59f00", maCalc(5)));
        series.push(maSer("MA10", "#3b82f6", maCalc(10)));
        series.push(maSer("MA20", "#8b5cf6", maCalc(20)));
        inst.setOption({
          backgroundColor: "#fff",
          tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
          legend: { top: 0, textStyle: { color: "#7a7f87", fontSize: 11 }, data: ["K线", "MA5", "MA10", "MA20"] },
          grid: { left: 8, right: 12, top: 28, bottom: 8, containLabel: true },
          xAxis: { type: "category", data: xs, axisLine: { lineStyle: { color: "#e3e5e8" } }, axisLabel: { color: "#7a7f87", fontSize: 10 } },
          yAxis: { type: "value", scale: true, axisLabel: { color: "#7a7f87", fontSize: 10 } },
          series,
        });
      } catch (e) {
        /* 日K加载失败不阻塞弹窗 */
      }
    });
    return () => { cancelAnimationFrame(raf); cancelled = true; if (inst) inst.dispose(); };
  }, [code, height]);
  return <div ref={ref} style={{ width: "100%", height }} />;
}

export default function ZTReviewTab() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [seatMenu, setSeatMenu] = useState(null);
  const [boardDetail, setBoardDetail] = useState(null);
  const [stockDetail, setStockDetail] = useState(null);
  const [klineFull, setKlineFull] = useState(false);
  const [openBoard, setOpenBoard] = useState(null); // 默认展开第一个板块，其余折叠
  const [selDate, setSelDate] = useState(""); // 历史功能：选择日期查看/补跑

  const handleDateChange = (v) => {
    if (!v) { setSelDate(""); return; }
    const dt = new Date(v + "T00:00:00");
    const wd = dt.getDay();
    if (wd === 0 || wd === 6) {
      // 默认禁止选择非交易日（周末；节假日由后端空值提示兜底）
      const recent = (data && data.date) || "";
      setError(v + " 为周末（非交易日，默认禁止选择），已自动切换为最近交易日 " + (recent || ""));
      setSelDate(recent);
      return;
    }
    setError("");
    setSelDate(v);
  };

  const load = async (date) => {
    setLoading(true);
    setError("");
    try {
      const d = await getZTTracking(date || undefined);
      setData(d);
    } catch (e) {
      setError(e && e.message ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const refresh = async (date) => {
    setRunning(true);
    setError("");
    try {
      const r = await runZTTracking(date ? [date] : null);
      let done = false;
      for (let i = 0; i < 90; i++) {
        await new Promise((res) => setTimeout(res, 2000));
        const j = await getZTJob(r.job_id);
        if (j.status === "done") { done = true; break; }
        if (j.status === "error") { setError(j.error || "采集失败"); break; }
      }
      if (done) await load();
    } catch (e) {
      setError(e && e.message ? e.message : "刷新失败");
    } finally {
      setRunning(false);
    }
  };

  const applySeatLocally = (name, type) => {
    setData((prev) => {
      if (!prev) return prev;
      const stocks = (prev.stocks || []).map((s) => {
        const seats = s.seats;
        if (!seats) return s;
        let touched = false;
        (["buy", "sell"]).forEach((side) => {
          (seats[side] || []).forEach((it) => {
            if (it.name === name) {
              it.type = type;
              it.type_label = SEAT_TYPE[type] || "未知";
              it.type_source = "manual";
              touched = true;
            }
          });
        });
        if (touched) {
          const counts = { youzi: 0, jigou: 0, lianghua: 0, waizi: 0, unknown: 0 };
          (["buy", "sell"]).forEach((side) => (seats[side] || []).forEach((it) => { counts[it.type] = (counts[it.type] || 0) + 1; }));
          seats.counts = counts;
        }
        return s;
      });
      return Object.assign({}, prev, { stocks });
    });
  };

  const markSeat = async (type) => {
    if (!seatMenu) return;
    const code = seatMenu.code;
    const name = seatMenu.name;
    try {
      await postZTTag({ code, seat_name: name, seat_type: type });
      applySeatLocally(name, type); // 乐观更新：点击即变
      setSeatMenu(null);
    } catch (e) {
      setError(e && e.message ? e.message : "保存标注失败");
      return;
    }
    try {
      await load(); // 再拉全量对齐后端
    } catch (e) {
      // 乐观更新已生效，后台刷新失败不阻断
    }
  };

  if (loading && !data) {
    return (
      <div className="card"><div className="spinner" /><div>正在加载涨停复盘数据…</div></div>
    );
  }
  if (!data || data.empty) {
    const msg = data && data.message ? data.message : (error || "暂无数据，请先采集");
    return (
      <div className="card">
        <div className="card-title">涨停复盘</div>
        <div className="card-state">{msg}</div>
        <button className="btn" onClick={refresh} disabled={running}>
          {running ? "采集中（约 10-20 秒）…" : "手动刷新 · 采集当日数据"}
        </button>
      </div>
    );
  }

  const sent = data.sentiment || {};
  const score = sent.emotion_score;
  const scoreValid = isFinite(score);
  const dashLen = scoreValid ? ((276.5 * score) / 100).toFixed(1) : "0";

  const tierRows = [
    ["5板+", data.tier["5板+"]], ["4板", data.tier["4板"]], ["3板", data.tier["3板"]],
    ["2板", data.tier["2板"]], ["首板", data.tier["首板"]],
  ];
  const maxTier = Math.max(1, tierRows[0][1] || 0, tierRows[1][1] || 0, tierRows[2][1] || 0, tierRows[3][1] || 0, tierRows[4][1] || 0);

  const rotByName = {};
  (data.rotation.boards || []).forEach((b) => { rotByName[b.board] = b; });
  // 只展示已入池题材；顺序按 rotation（重点在前、按连续天数排序）
  const rotOrder = {};
  (data.rotation.boards || []).forEach((r, idx) => { rotOrder[r.board] = idx; });
  const showBoards = (data.boards || [])
    .filter((b) => {
      const rb = rotByName[b.board];
      return !!(rb && rb.in_pool);
    })
    .sort((a, b2) => (rotOrder[a.board] != null ? rotOrder[a.board] : 999) - (rotOrder[b2.board] != null ? rotOrder[b2.board] : 999));

  const seats = [];
  (data.stocks || []).forEach((s) => {
    const se = s.seats || {};
    if ((se.buy || []).length || (se.sell || []).length) {
      seats.push({ code: s.code, name: s.name, board: s.topic || s.industry || "—", seats: se });
    }
  });

  return (
    <div className="zt-wrap">
      <div className="zt-topbar">
        <div className="zt-title">
          <span className="zt-date">{data.date || "—"}</span>
          <span className="zt-src">{data.pool && data.pool.source ? data.pool.source : ""}</span>
        </div>
        <div className="zt-toolbar">
          <input type="date" className="zt-date-input" value={selDate || data.date || ""}
            onChange={(e) => handleDateChange(e.target.value)} title="选择历史日期（查看/补跑；默认禁止非交易日）" />
          <button className="btn btn-ghost btn-sm" onClick={() => { if (selDate) load(selDate); }}>查看</button>
          <button className="btn btn-ghost btn-sm" onClick={() => refresh(selDate || null)} disabled={running}>
            {running ? "采集中…" : (selDate ? "采集该日" : "手动刷新")}
          </button>
        </div>
      </div>
      {error ? <div className="card-state zt-error">{error}</div> : null}

      <div className="card zt-card">
        <h3 className="card-title">市场天气</h3>
        <p className="card-sub">情绪温度 · {data.date}</p>
        <div className="weather-head">
          <div className="weather-big">
            <span className="weather-emoji">{WEATHER_ICON[sent.weather] || "☀️"}</span>
            {sent.weather || "数据缺失"}
          </div>
          <svg width="118" height="118" viewBox="0 0 118 118" aria-label="情绪温度环">
            <circle cx="59" cy="59" r="44" fill="none" stroke="#EFEDE8" strokeWidth="10" />
            <circle cx="59" cy="59" r="44" fill="none" stroke={scoreValid ? scoreColor(score) : "#adb5bd"}
              strokeWidth="10" strokeLinecap="round" strokeDasharray={dashLen + " 276.5"} transform="rotate(-90 59 59)" />
            <text x="59" y="57" textAnchor="middle" style={{ fontSize: 26, fontWeight: 800, fill: "#20242B" }}>
              {scoreValid ? Math.round(score) : "—"}
            </text>
            <text x="59" y="77" textAnchor="middle" style={{ fontSize: 10, fill: "#8A8F98" }}>情绪温度</text>
          </svg>
        </div>
        <div className="weather-label" style={scoreValid ? { color: scoreColor(score) } : undefined}>
          情绪温度 {scoreValid ? Math.round(score) : "—"} 分 · {sent.emotion_label || "—"}
        </div>
        <div className="weather-metrics">
          <span className="tag-chip light">上涨占比 <b>{pct(sent.up_down_ratio)}</b></span>
          <span className="tag-chip light">涨停 <b className="pos">{nOrDash(sent.limit_up_count)}</b></span>
          <span className="tag-chip light">跌停 <b className="neg">{nOrDash(sent.limit_down_count)}</b></span>
          <span className="tag-chip light">炸板率 <b>{pct(sent.break_rate)}</b></span>
          <span className="tag-chip light">连板高度 <b>{nOrDash(sent.lianban_height)}</b></span>
          <span className="tag-chip light">红盘率 <b>{pct(sent.red_rate)}</b></span>
        </div>
        {(sent.reasons || []).length ? (
          <div className="weather-reasons">
            <div className="rt-col-title">评分依据</div>
            {sent.reasons.map((r, i) => <div key={i} className="weather-reason">{r}</div>)}
          </div>
        ) : null}
      </div>

      <div className="card zt-card">
        <h3 className="card-title">今日涨停梯队</h3>
        <p className="card-sub">按连板数分组 · 已过滤 ST/退市/上市未满一年/北证</p>
        {tierRows.map((row) => {
          const w = Math.max(3, ((row[1] || 0) / maxTier) * 100) + "%";
          return (
            <div key={row[0]} className="zt-ladder-row">
              <span className="zt-ladder-lbl">{row[0]}</span>
              <div className="zt-ladder-bar"><i style={{ width: w }} /></div>
              <span className="zt-ladder-num">{row[1] || 0}</span>
            </div>
          );
        })}
        <div className="zt-legend">{data.filter_note ? "过滤：" + data.filter_note : ""}</div>
      </div>


      <div className="card zt-card">
        <h3 className="card-title">资讯影响</h3>
        <p className="card-sub">最新快讯（新浪7x24）· 利好/利空为关键词规则判定 · 命中主线题材板块/个股已标注 ★</p>
        {!((data.news || {}).items || []).length ? <div className="card-state">暂无 A 股相关快讯</div> : (
          <div className="zt-news">
            {(data.news.items || []).map((n, i) => (
              <div key={i} className="zt-news-item">
                <div className="zt-news-head">
                  <span className="zt-news-time">{n.time || ""}</span>
                  <span className={"zt-news-dir zt-news-" + (n.direction === "利好" ? "good" : n.direction === "利空" ? "bad" : "neu")}>{n.direction}</span>
                  <span className="zt-news-src">{n.source || ""}</span>
                  {n.mainline_hit ? <span className="zt-pill zt-pill-focus">影响主线题材</span> : null}
                </div>
                <div className="zt-news-text">{n.text}</div>
                {(n.boards || []).length ? (
                  <div className="zt-news-meta">影响板块：
                    {n.boards.map((b) => {
                      const main = ((data.news.main_boards) || []).indexOf(b) >= 0;
                      return <span key={b} className={"zt-chip" + (main ? " zt-role-core" : "")}>{b}{main ? " ★" : ""}</span>;
                    })}
                  </div>
                ) : null}
                {(n.stocks || []).length ? (
                  <div className="zt-news-meta">影响个股：
                    {n.stocks.map((s) => (
                      <span key={s.code} className="zt-chip zt-chip-seat zt-click" onClick={() => setStockDetail({ code: s.code, name: s.name, board: "" })}>
                        {s.name}({s.code}) ★主线
                      </span>
                    ))}
                  </div>
                ) : null}
                {n.searched ? (
                  <div className="zt-news-ev">
                    搜索佐证：{(n.evidence && n.evidence.ok && (n.evidence.results || []).length)
                      ? (n.evidence.results || []).map((r, j) => <div key={j}>· {r.title}</div>)
                      : ((n.evidence && n.evidence.note) || "未返回结果")}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card zt-card">
        <h3 className="card-title">主线题材</h3>
        <p className="card-sub">当日涨停 + 池内持续跟踪（含断板）一并展示；默认展开第一个板块，其余点击折叠展开；赚钱效应 = 均值+中位数打分排序；核心+前排同时大振幅 → 末期警告</p>
        {(() => {
          const bs = ((data.labels || {}).board_stats) || [];
          const ls = ((data.labels || {}).labels) || [];
          if (!bs.length) return <div className="card-state">当日无满足条件的题材板块</div>;
          return (
            <div className="zt-topic-table">
              {bs.map((st) => {
                const rows = ls.filter((l) => l.board === st.board);
                const rb = rotByName[st.board];
                const cur = openBoard !== null ? openBoard : (bs[0] ? bs[0].board : null);
                const open = cur === st.board;
                return (
                  <div key={st.board} className="zt-topic-group">
                    <div className={"zt-topic-group-head zt-click" + (st.warn ? " zt-warn-head" : "")}
                      onClick={() => setOpenBoard(cur === st.board ? null : st.board)}>
                      <span className="zt-caret">{open ? "▾" : "▸"}</span>
                      <span className="zt-board-name">{st.board}</span>
                      <button className="btn btn-ghost btn-sm" onClick={(e) => { e.stopPropagation(); setBoardDetail({ board: st.board, from: "topics" }); }}>股票池</button>
                      {rb && rb.focus ? <span className="zt-pill zt-pill-focus">重点关注</span> : (rb && rb.in_pool ? <span className="zt-pill zt-pill-rot">轮动</span> : null)}
                      {rb && rb.days5 != null ? <span className="zt-pill">5日{rb.days5}天 · 10日{rb.days10}天</span> : null}
                      <span className={"zt-pill" + (st.earn_score != null && st.earn_score >= 10 ? " zt-up" : "")}>赚钱效应 {st.earn_score != null ? st.earn_score + "分" : "—"}</span>
                      <span className="zt-pill">当日涨停 {st.zt_count_today} / 断板 {st.trend_count}</span>
                      {st.earn_mean != null ? <span className="zt-pill">均值 {(st.earn_mean * 100).toFixed(1)}% · 中位 {(st.earn_median * 100).toFixed(1)}%</span> : null}
                      {st.warn ? <span className="zt-pill zt-pill-warn">⚠️ {st.warn_reason}</span> : null}
                    </div>
                    {open ? (
                    <table className="zt-table">
                      <thead><tr><th>当日涨停</th><th>代码</th><th>名称</th><th>连板</th><th>累计涨幅</th><th>振幅</th><th>身份</th><th>操作</th></tr></thead>
                      <tbody>
                        {rows.map((l) => {
                          const roleCls = l.role.indexOf("核心") >= 0 ? " zt-role-core" : (l.role.indexOf("中军") >= 0 ? " zt-role-host" : " zt-role-weak");
                          const actCls = l.action.indexOf("低吸") >= 0 ? " zt-act-buy" : (l.action.indexOf("追涨") >= 0 ? " zt-act-chase" : (l.action.indexOf("回避") >= 0 ? " zt-act-avoid" : " zt-act-wait"));
                          return (
                            <tr key={l.code} className="zt-click" onClick={() => setStockDetail({ code: l.code, name: l.name, board: l.board })}>
                              <td>{l.limit_up ? <span className="zt-up">是</span> : <span className="zt-muted">断板</span>}</td>
                              <td>{l.code}</td>
                              <td><b>{l.name}</b>{l.leader ? " ★" : ""}</td>
                              <td>{l.lianban}板</td>
                              <td className={l.cum_pct != null ? (l.cum_pct >= 0 ? " zt-up" : " zt-down") : ""}>{l.cum_pct != null ? (l.cum_pct * 100).toFixed(1) + "%" : "—(今板)"}</td>
                              <td>{l.amplitude != null ? (l.amplitude * 100).toFixed(0) + "%" : "—"}</td>
                              <td><span className={"zt-chip" + roleCls}>{l.role}</span></td>
                              <td><span className={"zt-chip" + actCls}>{l.action}</span></td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                    ) : (
                      <div className="zt-collapsed zt-click" onClick={() => setOpenBoard(st.board)}>点击展开（{rows.length} 只 · 当日涨停 {st.zt_count_today} / 持续跟踪 {st.trend_count}）</div>
                    )}
                  </div>
                );
              })}
            </div>
          );
        })()}
      </div>

      <div className="card zt-card">
        <h3 className="card-title">龙虎榜席位</h3>
        <p className="card-sub">规则：机构专用=机构；命中名单/人工标注=游资/量化；其余=未知（点击标注）</p>
        {!seats.length ? <div className="card-state">当日无上榜席位数据</div> : (
          <table className="zt-table">
            <thead><tr><th>股票</th><th>题材</th><th>买 席位</th><th>卖 席位</th><th>类型汇总</th></tr></thead>
            <tbody>
              {seats.slice(0, 30).map((row, idx) => (
                <tr key={row.code + idx}>
                  <td><b>{row.name}</b> <span className="zt-muted">{row.code}</span></td>
                  <td>{row.board}</td>
                  <td>{(row.seats.buy || []).slice(0, 4).map((s, i) => (
                    <span key={i} className="zt-chip zt-seat"
                      style={{ color: SEAT_COLOR[s.type] || "#868E96", borderColor: SEAT_COLOR[s.type] || "#E6E4DF" }}
                      onClick={() => setSeatMenu({ code: row.code, name: s.name, type: s.type })}>
                      {s.name.replace(/证券营业部$/, "")} · {s.type_label || "未知"}
                    </span>
                  ))}</td>
                  <td>{(row.seats.sell || []).slice(0, 4).map((s, i) => (
                    <span key={i} className="zt-chip zt-seat"
                      style={{ color: SEAT_COLOR[s.type] || "#868E96", borderColor: SEAT_COLOR[s.type] || "#E6E4DF" }}
                      onClick={() => setSeatMenu({ code: row.code, name: s.name, type: s.type })}>
                      {s.name.replace(/证券营业部$/, "")} · {s.type_label || "未知"}
                    </span>
                  ))}</td>
                  <td>{(row.seats.counts ? Object.keys(row.seats.counts) : []).filter((t) => row.seats.counts[t]).map((t) => (
                    <span key={t} className="zt-chip" style={{ color: SEAT_COLOR[t] }}>{SEAT_TYPE[t]}×{row.seats.counts[t]}</span>
                  ))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="zt-foot">数据均为程序采集（来源见各卡）；席位类型为规则+人工标注结果。仅供参考，不构成投资建议。</div>

      {boardDetail ? (
        <div className="zt-modal" onClick={() => setBoardDetail(null)}>
          <div className="zt-modal-box zt-wide" onClick={(e) => e.stopPropagation()}>
            <div className="zt-modal-title">{boardDetail.board} · 股票池</div>
            {(() => {
              const rb = data.rotation.boards.find((x) => x.board === boardDetail.board) || {};
              const poolRec = data.pools ? data.pools[boardDetail.board] : null;
              const dayStocks = (data.stocks || [])
                .filter((s) => (s.topic || s.industry) === boardDetail.board)
                .sort((a, b) => ((b.lianban || 1) - (a.lianban || 1)) || String(a.first_seal || "").localeCompare(String(b.first_seal || "")));
              const cumMap = (poolRec && poolRec.cumulative) ? poolRec.cumulative : {};
              const trackedStocks = (poolRec && poolRec.tracked && poolRec.tracked.length)
                ? poolRec.tracked.slice().sort((a, b) => {
                    const ca = cumMap[a.code] ? cumMap[a.code].cum_pct : null;
                    const cb = cumMap[b.code] ? cumMap[b.code].cum_pct : null;
                    return (cb == null ? -Infinity : cb) - (ca == null ? -Infinity : ca);
                  })
                : ((poolRec && poolRec.entry_snapshot) ? poolRec.entry_snapshot : []);
              return (
                <div className="zt-board-pool">
                  <div className="zt-pool-summary">
                    <span>5日涨停 <b>{rb.days5 != null ? rb.days5 : "—"}</b> 天</span>
                    <span>10日涨停 <b>{rb.days10 != null ? rb.days10 : "—"}</b> 天</span>
                    <span>今日涨停 <b>{rb.zt_count_today != null ? rb.zt_count_today : dayStocks.length}</b> 家</span>
                    <span>最高 <b>{rb.max_ladder ? rb.max_ladder + "板" : "—"}</b></span>
                    <span>标签 <b>{rb.tag || "观察"}</b></span>
                    {poolRec ? <span>准入 <b>{poolRec.max_zt10 != null ? "单日最高" + poolRec.max_zt10 + "家涨停（≥10）" : "—"}</b></span> : null}
                    {poolRec ? <span>池状态 <b>{poolRec.status === "active" ? "跟踪中（入池 " + (poolRec.entry_date || "—") + "）" : "已出池（" + (poolRec.exit_date || "—") + "）"}</b></span> : null}
                  </div>
                  <div className="zt-pool-section">当日涨停股（按连板梯队排序，点击可标注席位）</div>
                  <table className="zt-table">
                    <thead><tr><th>代码</th><th>名称</th><th>连板</th><th>首封</th><th>流通市值</th><th>板型</th><th>标签</th><th>席位</th></tr></thead>
                    <tbody>
                      {dayStocks.slice(0, 40).map((s) => {
                        const lbd = ((data.labels || {}).labels || []).find((x) => x.code === s.code);
                        const tagTxt = lbd ? (lbd.role.indexOf("核心") >= 0 ? "核心" : (lbd.role.indexOf("中军") >= 0 ? "中军" : (lbd.role.indexOf("杂毛") >= 0 ? "杂毛" : "跟风"))) : "—";
                        const tagCls = tagTxt === "核心" ? " zt-up" : (tagTxt === "中军" ? " zt-role-host" : (tagTxt === "杂毛" ? " zt-role-weak" : ""));
                        return (
                          <tr key={s.code}>
                            <td>{s.code}</td><td><b>{s.name}</b></td><td>{s.lianban || 1}</td>
                            <td>{sealTime(s.first_seal)}</td>
                            <td>{s.cap_float != null ? amt(s.cap_float) : "—"}</td>
                            <td>{s.board_type || "—"}</td>
                            <td><span className={"zt-chip" + tagCls}>{tagTxt}</span></td>
                            <td>{((s.seats && s.seats.counts) ? Object.keys(s.seats.counts).filter((t) => s.seats.counts[t]).map((t) => SEAT_TYPE[t] + "×" + s.seats.counts[t]).join(" ") : "—")}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  {poolRec ? (
                    <div>
                      <div className="zt-pool-section">池内跟踪股（自统计日起全部涨停股，按累计涨跌幅排序，历史不删）</div>
                      <div className="zt-pool-codes">
                        {trackedStocks.map((e) => {
                          const cumRec = poolRec.cumulative ? poolRec.cumulative[e.code] : null;
                          const cum = cumRec ? cumRec.cum_pct : null;
                          const cumTxt = cum == null ? "累计—" : "累计" + (cum >= 0 ? "+" : "") + (cum * 100).toFixed(1) + "%";
                          const cls = cum == null ? "" : (cum >= 0 ? " zt-up" : " zt-down");
                          const lbdChip = (((data.labels || {}).labels) || []).find((x) => x.code === e.code);
                          const isCore = !!(lbdChip && (lbdChip.role.indexOf("核心") >= 0 || lbdChip.role.indexOf("中军") >= 0));
                          return (
                            <span key={e.code} className={"zt-chip zt-pool-stk" + cls + (isCore ? " zt-role-core" : "")}
                              onClick={() => { setStockDetail({ code: e.code, name: e.name, board: boardDetail.board, lianban: e.lianban }); setBoardDetail(null); }}>
                              {e.name}({e.code})·{e.lianban ? e.lianban + "板" : "首板"}·{cumTxt}{isCore ? " ★" : ""}
                            </span>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}
                </div>
              );
            })()}
          </div>
        </div>
      ) : null}

      {stockDetail ? (
        <div className="zt-modal" onClick={() => setStockDetail(null)}>
          <div className="zt-modal-box zt-wide" onClick={(e) => e.stopPropagation()}>
            <div className="zt-modal-title">{stockDetail.name || stockDetail.code} · 个股详情</div>
            {(() => {
              const s = (data.stocks || []).find((x) => x.code === stockDetail.code) || {};
              const sig = (data.signal.items || []).find((x) => x.code === stockDetail.code);
              const seats = s.seats || {};
              return (
                <div className="zt-stock-detail">
                  <div className="zt-stock-row"><span>代码</span><b>{stockDetail.code}</b></div>
                  <div className="zt-stock-row"><span>题材</span><b>{s.topic || stockDetail.board || "—"}</b></div>
                  <div className="zt-stock-row"><span>涨停归属来源</span><b>{s.topic_source || "—"}</b></div>
                  <div className="zt-stock-row"><span>连板</span><b>{s.lianban || stockDetail.lianban || 1} 板</b></div>
                  <div className="zt-stock-row"><span>板型</span><b>{s.board_type || "—"}（{s.seal_derived === "tushare" ? "精确口径" : "近似口径"}）</b></div>
                  <div className="zt-stock-row"><span>首次封板</span><b>{sealTime(s.first_seal)}</b></div>
                  <div className="zt-stock-row"><span>流通市值</span><b>{s.cap_float != null ? amt(s.cap_float) : "—"}（{s.cap_class || "—"}）</b></div>
                  <div className="zt-stock-row"><span>席位</span><b>{(seats.counts ? Object.keys(seats.counts).filter((t) => seats.counts[t]).map((t) => SEAT_TYPE[t] + "×" + seats.counts[t]).join("，") : "—")}</b></div>
                  <div className="zt-stock-row"><span>介入方向</span><b>{sig ? sig.signal + "（" + sig.note + "）" : "观察"}</b></div>
                  {s.reason ? <div className="zt-stock-row"><span>涨停原因</span><b>{s.reason}</b></div> : null}
                  {s.reason_detail ? <div className="zt-stock-row zt-note"><span>原因详解</span><b>{s.reason_detail.slice(0, 400)}</b></div> : null}
{(() => {
                      const lbd = ((data.labels || {}).labels || []).find((x) => x.code === stockDetail.code);
                      if (!lbd) return null;
                      const roleCls = lbd.role.indexOf("核心") >= 0 ? "zt-role-core" : (lbd.role.indexOf("中军") >= 0 ? "zt-role-host" : "zt-role-weak");
                      const actCls = lbd.action.indexOf("低吸") >= 0 ? "zt-act-buy" : (lbd.action.indexOf("追涨") >= 0 ? "zt-act-chase" : (lbd.action.indexOf("回避") >= 0 ? "zt-act-avoid" : "zt-act-wait"));
                      return (
                        <div className="zt-label-block">
                          <div className="zt-label-row"><span>身份</span><b className={"zt-role " + roleCls}>{lbd.role}（{lbd.role_reason.join("；")}）</b></div>
                          <div className="zt-label-row"><span>操作</span><b className={"zt-act " + actCls}>{lbd.action}</b> <em style={{ color: "#555", fontSize: 12 }}>{lbd.action_reason.join("；")}</em></div>
                          {lbd.heat_level ? <div className="zt-label-row"><span>热度</span><b>{lbd.heat_level}（{lbd.heat} 分）{lbd.leader ? " · 板块龙头（人气共识）" : ""}{lbd.limit_up ? "" : " · 未涨停"}</b></div> : null}
                          {lbd.cum_pct != null ? <div className="zt-label-row"><span>累计</span><b className={(lbd.cum_pct >= 0 ? " zt-up" : " zt-down")}>{(lbd.cum_pct * 100).toFixed(1)}%</b></div> : null}
                          {lbd.next_day ? <div className="zt-label-row zt-note"><span>次日</span><b>{lbd.next_day.plan}</b></div> : null}
                          {lbd.next_day ? <div className="zt-label-row zt-note"><span>验证</span><b>{lbd.next_day.checklist.join("；")}</b></div> : null}
                          {lbd.next_day ? <div className="zt-label-row zt-note"><span>退出</span><b>{lbd.next_day.exits.join("；")}</b></div> : null}
                        </div>
                      );
                    })()}
                  <div className="zt-kline-title">
                    日K（近 60 个交易日 · MA5/10/20）
                    <button className="btn btn-ghost btn-sm" onClick={() => setKlineFull(true)}>放大</button>
                  </div>
                  <ZTKlineChart code={stockDetail.code} />
                </div>
              );
            })()}
          </div>
        </div>
      ) : null}

      {klineFull && stockDetail ? (
        <div className="zt-modal" onClick={() => setKlineFull(false)}>
          <div className="zt-modal-box zt-kline-box" onClick={(e) => e.stopPropagation()}>
            <div className="zt-kline-head">
              <span>{stockDetail.name || stockDetail.code} · 日K（近 60 个交易日 · MA5/10/20）</span>
              <button className="btn btn-ghost btn-sm" onClick={() => setKlineFull(false)}>关闭</button>
            </div>
            <ZTKlineChart code={stockDetail.code} height={560} />
          </div>
        </div>
      ) : null}

      {seatMenu ? (
        <div className="zt-modal" onClick={() => setSeatMenu(null)}>
          <div className="zt-modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="zt-modal-title">标注席位类型</div>
            <div className="zt-modal-seat">{seatMenu.name}</div>
            <div className="zt-modal-actions">
              {Object.keys(SEAT_TYPE).filter((t) => t !== "unknown").map((t) => (
                <button key={t} className="btn" style={{ background: SEAT_COLOR[t], margin: 4 }} onClick={() => markSeat(t)}>{SEAT_TYPE[t]}</button>
              ))}
              <button className="btn btn-ghost" onClick={() => setSeatMenu(null)}>取消</button>
            </div>
          </div>
        </div>
      ) : null}

      <style>{ZT_CSS}</style>
    </div>
  );
}
