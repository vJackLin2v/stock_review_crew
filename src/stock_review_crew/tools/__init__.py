"""Tools 模块：数据层（I1）纯函数 + LangChain @tool 包装"""

from .stock_data import (
    # 纯函数接口（返回 dict，供测试/FastAPI 复用）
    fetch_market_micro, fetch_index_trend, fetch_market_macro, fetch_stock_info,
    fetch_sentiment, fetch_news_headlines, fetch_auction_quote, fetch_minute_data,
    fetch_lhb, fetch_zt_pool, fetch_market_breadth, fetch_sectors,
    fetch_pre_market, fetch_auction, fetch_intraday_am, fetch_noon,
    fetch_intraday_pm, fetch_close, fetch_mode_data,
    # LangChain @tool 接口（返回 JSON 字符串，供 graph.py 调用）
    get_market_micro, get_index_trend, get_market_macro, get_stock_info,
    get_sentiment, get_news_headlines, get_auction_quote, get_minute_data,
    get_lhb, get_pre_market, get_auction, get_intraday_am, get_noon,
    get_intraday_pm, get_close, get_mode_data, search_history,
)

from .tdx_local import (
    read_day, read_minline, read_day_tail, iter_day_codes,
    resolve_market, day_file_path, minline_file_path, TdxError,
)

from .market_extra import (
    compute_market_weather, fetch_market_weather, fetch_us_nasdaq,
    fetch_sector_heat, fetch_extra_indices, fetch_style_quad,
)

__all__ = [
    "fetch_market_micro", "fetch_index_trend", "fetch_market_macro", "fetch_stock_info",
    "fetch_sentiment", "fetch_news_headlines", "fetch_auction_quote", "fetch_minute_data",
    "fetch_lhb", "fetch_zt_pool", "fetch_market_breadth", "fetch_sectors",
    "fetch_pre_market", "fetch_auction", "fetch_intraday_am", "fetch_noon",
    "fetch_intraday_pm", "fetch_close", "fetch_mode_data",
    "get_market_micro", "get_index_trend", "get_market_macro", "get_stock_info",
    "get_sentiment", "get_news_headlines", "get_auction_quote", "get_minute_data",
    "get_lhb", "get_pre_market", "get_auction", "get_intraday_am", "get_noon",
    "get_intraday_pm", "get_close", "get_mode_data", "search_history",
    "read_day", "read_minline", "read_day_tail", "iter_day_codes",
    "resolve_market", "day_file_path", "minline_file_path", "TdxError",
    "compute_market_weather", "fetch_market_weather", "fetch_us_nasdaq",
    "fetch_sector_heat", "fetch_extra_indices", "fetch_style_quad",
]
