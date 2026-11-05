import React, { useEffect, useRef, useState } from "react";

/**
 * 多选下拉组件：用于分析师多选（回测/复盘/聊天等）。
 * props:
 *   options: [{id, skill_name, role?}]
 *   value: string[] 已选 id
 *   onChange: (ids: string[]) => void
 *   placeholder: string
 *   label: string 可选标题
 */
export default function MultiSelect({ options = [], value = [], onChange, placeholder = "请选择", label = "" }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  useEffect(() => {
    const onDocClick = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const allIds = options.map((o) => o.id);
  const selected = options.filter((o) => value.includes(o.id));
  const allSelected = allIds.length > 0 && allIds.every((id) => value.includes(id));

  const toggle = (id) => {
    const next = value.includes(id) ? value.filter((x) => x !== id) : [...value, id];
    onChange(next);
  };

  const toggleAll = () => onChange(allSelected ? [] : allIds);

  return (
    <div ref={rootRef} className="multi-select" style={{ position: "relative", minWidth: 220 }}>
      {label ? <div className="form-label" style={{ marginBottom: 4 }}>{label}</div> : null}
      <button
        type="button"
        className="multi-select-trigger"
        style={{
          width: "100%",
          padding: "6px 10px",
          border: "1px solid #ccc",
          borderRadius: 6,
          background: "#fff",
          fontSize: 13,
          textAlign: "left",
          cursor: "pointer",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 8,
        }}
        onClick={() => setOpen((o) => !o)}
      >
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {selected.length === 0
            ? placeholder
            : selected.length === options.length
              ? `全部分析师（${options.length}）`
              : `${selected.map((o) => o.skill_name).join("、")}（${selected.length}/${options.length}）`}
        </span>
        <span style={{ fontSize: 10, color: "#888" }}>{open ? "▲" : "▼"}</span>
      </button>
      {open ? (
        <div
          className="multi-select-dropdown"
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            right: 0,
            marginTop: 4,
            border: "1px solid #ddd",
            borderRadius: 8,
            background: "#fff",
            boxShadow: "0 6px 18px rgba(0,0,0,.12)",
            zIndex: 20,
            maxHeight: 280,
            overflow: "auto",
            padding: 6,
          }}
        >
          <button
            type="button"
            className="multi-select-all"
            style={{
              display: "block",
              width: "100%",
              padding: "5px 8px",
              border: "1px dashed #aaa",
              borderRadius: 6,
              background: "transparent",
              cursor: "pointer",
              fontSize: 12,
              marginBottom: 4,
            }}
            onClick={toggleAll}
          >
            {allSelected ? "取消全选" : "全选"}
          </button>
          <div style={{ borderTop: "1px solid #eee", marginBottom: 4 }} />
          {options.map((opt) => {
            const on = value.includes(opt.id);
            return (
              <label
                key={opt.id}
                className="multi-select-option"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "6px 8px",
                  borderRadius: 6,
                  cursor: "pointer",
                  fontSize: 13,
                  background: on ? "#fff2e8" : "transparent",
                }}
              >
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => toggle(opt.id)}
                  style={{ margin: 0, cursor: "pointer" }}
                />
                <span style={{ flex: 1 }}>
                  {opt.skill_name}
                  {opt.role ? <span style={{ color: "#999", fontSize: 11, marginLeft: 6 }}>{opt.role}</span> : null}
                </span>
              </label>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}