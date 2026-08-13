import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";
import { applyToDom, loadSettings } from "./settings";

// 渲染前应用本地设置（主题/涨跌配色/字号），避免首帧闪烁
applyToDom(loadSettings());

createRoot(document.getElementById("root")).render(<App />);
