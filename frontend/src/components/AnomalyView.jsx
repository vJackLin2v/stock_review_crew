// 2026-08-17：监管异动 · 单股计算（复刻图片 UI）
import React, { useState } from "react";
import * as api from "../api";
import { fmtSignedPct, num, pctClass } from "../format";

const fmt2 = (v, suffix = "") => {
  const n = num(v, NaN);
  return Number.isFinite(n) ? `${n.toFixed(2)}${suffix}` : "—";
};

function WinCard({ label, w, alert }) {
  if (!w) return null;
  return (
    <div className={`anom-win${alert ? " warn" : ""}`}>
      <div className="anom-win-head">
        <span className="anom-win-label">{label}偏离</span>
        <span className="anom-win-value">{fmt2(w.current, "%")}</span>
      </div>
      <div className="anom-win-thr">
        阈值 {fmt2(w.threshold_up_pp, "%")} / {fmt2(w.threshold_down_pp, "%")}
      </div>
      <div className="anom-win-row">区间最大 <b>{fmt2(w.max, "%")}</b>（{w.max_days}天）</div>
      <div className="anom-win-row">触发空间 <b className="pos">{fmt2(w.remain_up_pp, "%")}</b></div>
      <div className="anom-win-row">触发价 <b>￥{fmt2(w.trigger_up_price)} / ￥{fmt2(w.trigger_down_price)}</b></div>
    </div>
  );
}

export default function AnomalyView({ onBack }) {
  const [code, setCode] = useState("600664");
  const [days, setDays] = useState(2);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [data, setData] = useState(null);

  const run = async () => {
    if (!/^\d{6}$/.test(code.trim())) {
      setError("请输入 6 位股票代码");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const res = await api.getAnomalyStock(code.trim(), days);
      setData(res);
    } catch (err) {
      setData(null);
      setError((err && err.message) || "异动计算失败");
    } finally {
      setLoading(false);
    }
  };

  const w = data && data.windows ? data.windows : {};
  const sd = w.same_direction || {};
  const alerts = data && data.alerts ? data.alerts : {};
  const sims = data && data.simulation ? data.simulation : [];

  return (
    <div className="grid" style={{ gap: 16 }}>
      <div className="card">
        <div className="accounts-header">
          <button type="button" className="btn btn-outline btn-sm" onClick={onBack}>← 返回首页</button>
          <div>
            <h3 className="card-title" style={{ marginBottom: 0 }}>监管异动 · 单股计算</h3>
            <p className="card-sub">主板/创业板/科创板 涨跌幅偏离异动监控 · 仅供参考，不构成投资建议</p>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="set-options" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="6 位代码，如 600664"
            style={{ padding: "6px 8px", width: 150 }}
          />
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} style={{ padding: "6px 8px" }}>
            {[1, 2, 3, 5].map((d) => <option key={d} value={d}>{d} 天</option>)}
          </select>
          <button type="button" className="set-option active" onClick={run} disabled={loading}>
            <span className="set-option-label">{loading ? "计算中…" : "计算异动"}</span>
          </button>
        </div>
        <p className="card-sub">免费用户每天可使用 1 次单股计算，会员不限次数。</p>
        {error ? <div className="bt-error">{error}</div> : null}
      </div>

      {data ? (
        <>
          {/* 异动状态 */}
          <div className="card">
            <div className="anom-status-row">
              <span className={`anom-status ${alerts.severe ? "warn" : "ok"}`}>
                {alerts.severe ? "SEVERE 严重异动" : "NORMAL 状态正常"}
              </span>
              <span className="anom-stock-code">{data.name ? `${data.name}（${data.code}）` : `股票代码 ${data.code}`}</span>
              <span className="anom-close">当前股价 <b>￥{fmt2(data.close)}</b></span>
              <span className={`anom-pct ${pctClass(data.stock_pct)}`}>{fmtSignedPct(data.stock_pct)}</span>
            </div>
            <p className="card-sub">对比基准：{data.board} → {data.index_name}</p>

            <div className="anom-wins">
              <WinCard label="3日" w={w["3d"]} alert={alerts["3d"]} />
              <WinCard label="10日" w={w["10d"]} alert={alerts["10d"]} />
              <WinCard label="30日" w={w["30d"]} alert={alerts["30d"]} />
            </div>

            <div className="anom-same">
              同向异动：已 {sd.count || 0} 次（{sd.dir || "—"}），还差 {Math.max((sd.threshold || 4) - (sd.count || 0), 0)} 次（≥{sd.threshold || 4} 次触发严重）
            </div>
          </div>

          {/* 未来模拟 */}
          {sims.length ? (
            <div className="card">
              <div className="card-head-row">
                <div>
                  <h3 className="card-title">未来交易日模拟计算</h3>
                  <p className="card-sub">基准指数走平假设；安全空间按 30 日/10 日限更小者</p>
                </div>
              </div>
              <div className="accounts-table-wrap">
                <table className="accounts-table">
                  <thead><tr><th>T</th><th>日期</th><th>3日偏离</th><th>10日偏离</th><th>30日偏离</th><th>安全上涨空间</th></tr></thead>
                  <tbody>
                    {sims.map((s) => (
                      <tr key={s.t}>
                        <td>T+{s.t}（{s.date}）</td>
                        <td>{s.date}</td>
                        <td className={pctClass(s.dev3)}>{fmt2(s.dev3, "%")}</td>
                        <td className={pctClass(s.dev10)}>{fmt2(s.dev10, "%")}</td>
                        <td className={pctClass(s.dev30)}>{fmt2(s.dev30, "%")}</td>
                        <td className="pos">{fmt2(s.safe_upside_pct, "%")}（30日限）</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          {/* 30 天异动计算详情 */}
          {data.daily_30 && data.daily_30.length ? (
            <div className="card">
              <h3 className="card-title">30 天异动计算详情</h3>
              <div className="accounts-table-wrap">
                <table className="accounts-table">
                  <thead><tr><th>日期</th><th>3日偏离</th><th>10日最大偏离</th></tr></thead>
                  <tbody>
                    {data.daily_30.map((d) => (
                      <tr key={d.date}>
                        <td>{d.date}</td>
                        <td className={pctClass(d.dev3)}>{fmt2(d.dev3, "%")}</td>
                        <td className={pctClass(d.dev10)}>{fmt2(d.dev10, "%")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="card-sub" style={{ marginTop: 8 }}>
                数据源：个股 {data.sources && data.sources.stock} · 指数 {data.sources && data.sources.index}
              </p>
            </div>
          ) : null}

          <div className="card">
            <div className="disclaimer"><span>⚠️</span><div><b>免责声明：</b>{data.disclaimer || "仅供参考，不构成投资建议"}</div></div>
          </div>
        </>
      ) : loading ? (
        <div className="card loading-card"><div className="spinner" />正在计算异动…</div>
      ) : (
        <div className="card bt-empty-state"><div className="bt-empty-title">输入股票代码，点击“计算异动”</div></div>
      )}
    </div>
  );
}
