"""交割单解析器单元测试（T4，自 synalysis_crew tests/test_parser.py 移植）。

覆盖：10 种操作类型、中途开始场景、列序/表头空格/标题行容忍、费用聚合、
UNKNOWN 兜底、空行跳过、0 金额保留、日期多格式、CSV/TXT 分隔符探测与编码链、
涨乐财富通版式（摘要映射/数量符号/费用映射/cash_amount/无名称列）、
中文错误信息、合同号脱敏。全部临时目录，不写仓库 data/。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import uuid
from datetime import date, datetime
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stock_review_crew.trades import (  # noqa: E402
    OP_LABELS,
    OpType,
    ParseError,
    TradeRecord,
    parse_trades,
)


REAL_TEN_OPS = {
    OpType.BUY,
    OpType.SELL,
    OpType.BANK_TO_SEC,
    OpType.SEC_TO_BANK,
    OpType.REPO,
    OpType.INTEREST,
    OpType.DIVIDEND,
    OpType.BONUS_SHARE,
    OpType.DIVIDEND_DIFF,
    OpType.DESIGNATED_TRADE,
}


@pytest.fixture()
def tmp_path():
    """自建临时目录（替代 pytest 内置 tmp_path，规避 Windows 沙箱 ACL 问题）。"""
    base = Path(tempfile.gettempdir())
    path = base / f"trades_parser_{uuid.uuid4().hex[:12]}"
    path.mkdir(mode=0o777)
    yield path
    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# 合成夹具（虚构数据，全部内联于测试文件；真实交割单不进入仓库）
# ---------------------------------------------------------------------------

STANDARD_HEADERS = [
    "证券代码",
    "证券名称",
    "操作",
    "成交数量",
    "成交均价",
    "成交金额",
    "股票余额",
    "发生金额",
    "手续费",
    "印花税",
    "其他杂费",
    "资金余额",
    "合同编号",
    "交收日期",
    "证券中文全称",
    "佣金",
    "过户费",
    "清算费(B股)",
    "币种",
]

ZHANGLE_HEADERS = [
    "日期",
    "摘要",
    "发生金额",
    "委托号",
    "过户费",
    "其他杂费",
    "手续费/佣金",
    "印花税",
    "成交金额",
    "股票代码",
    "成交数量",
    "成交价格",
]


def make_trades(include_unknown: bool = False) -> list[TradeRecord]:
    """返回覆盖 10 种真实操作的合成交割记录（含「中途开始」场景）。"""
    trades = [
        TradeRecord(
            code="600519", name="贵州茅台", op_type=OpType.SELL, qty=100.0,
            price=1500.00, amount=150000.00, balance=149818.50, fee=30.00,
            stamp_tax=150.00, commission=30.00, transfer_fee=1.50,
            contract_no="AD00000001", trade_date=date(2025, 11, 27),
            currency="人民币",
        ),
        TradeRecord(
            code="", name="", op_type=OpType.BANK_TO_SEC, qty=0.0, price=0.0,
            amount=0.0, balance=199818.50, fee=0.0, stamp_tax=0.0,
            commission=0.0, transfer_fee=0.0, contract_no="",
            trade_date=date(2025, 11, 28), currency="人民币",
        ),
        TradeRecord(
            code="000001", name="平安银行", op_type=OpType.BUY, qty=1000.0,
            price=10.50, amount=10500.00, balance=189313.50, fee=5.00,
            stamp_tax=0.0, commission=5.00, transfer_fee=0.0,
            contract_no="AD00000002", trade_date=date(2025, 11, 28),
            currency="人民币",
        ),
        TradeRecord(
            code="000001", name="平安银行", op_type=OpType.BUY, qty=500.0,
            price=10.80, amount=5400.00, balance=183908.50, fee=5.00,
            stamp_tax=0.0, commission=5.00, transfer_fee=0.0,
            contract_no="AD00000003", trade_date=date(2025, 12, 2),
            currency="人民币",
        ),
        TradeRecord(
            code="000001", name="平安银行", op_type=OpType.BONUS_SHARE, qty=150.0,
            price=0.0, amount=0.0, balance=183908.50, fee=0.0, stamp_tax=0.0,
            commission=0.0, transfer_fee=0.0, contract_no="",
            trade_date=date(2026, 1, 5), currency="人民币",
        ),
        TradeRecord(
            code="000001", name="平安银行", op_type=OpType.DIVIDEND, qty=1000.0,
            price=0.0, amount=500.00, balance=184408.50, fee=0.0, stamp_tax=0.0,
            commission=0.0, transfer_fee=0.0, contract_no="",
            trade_date=date(2026, 1, 5), currency="人民币",
        ),
        TradeRecord(
            code="000001", name="平安银行", op_type=OpType.DIVIDEND_DIFF, qty=0.0,
            price=0.0, amount=0.0, balance=184308.50, fee=0.0, stamp_tax=0.0,
            commission=0.0, transfer_fee=0.0, contract_no="",
            trade_date=date(2026, 1, 6), currency="人民币",
        ),
        TradeRecord(
            code="000001", name="平安银行", op_type=OpType.SELL, qty=800.0,
            price=11.20, amount=8960.00, balance=193254.44, fee=5.00,
            stamp_tax=8.96, commission=5.00, transfer_fee=0.10,
            contract_no="AD00000004", trade_date=date(2026, 1, 7),
            currency="人民币",
        ),
        TradeRecord(
            code="131810", name="Ｒ-001", op_type=OpType.REPO, qty=10.0,
            price=100.00, amount=1000.00, balance=192254.34, fee=0.10,
            stamp_tax=0.0, commission=0.10, transfer_fee=0.0,
            contract_no="AD00000005", trade_date=date(2026, 1, 8),
            currency="人民币",
        ),
        TradeRecord(
            code="131810", name="Ｒ-001", op_type=OpType.REPO, qty=10.0,
            price=100.005, amount=1000.05, balance=193254.39, fee=0.0,
            stamp_tax=0.0, commission=0.0, transfer_fee=0.0,
            contract_no="AD00000005", trade_date=date(2026, 1, 9),
            currency="人民币",
        ),
        TradeRecord(
            code="", name="", op_type=OpType.INTEREST, qty=0.0, price=0.0,
            amount=0.0, balance=193277.84, fee=0.0, stamp_tax=0.0,
            commission=0.0, transfer_fee=0.0, contract_no="",
            trade_date=date(2026, 1, 10), currency="人民币",
        ),
        TradeRecord(
            code="", name="", op_type=OpType.SEC_TO_BANK, qty=0.0, price=0.0,
            amount=0.0, balance=173277.84, fee=0.0, stamp_tax=0.0,
            commission=0.0, transfer_fee=0.0, contract_no="",
            trade_date=date(2026, 1, 12), currency="人民币",
        ),
        TradeRecord(
            code="799999", name="指定登记", op_type=OpType.DESIGNATED_TRADE,
            qty=0.0, price=0.0, amount=0.0, balance=173277.84, fee=0.0,
            stamp_tax=0.0, commission=0.0, transfer_fee=0.0,
            contract_no="0220002602", trade_date=date(2026, 1, 13),
            currency="人民币",
        ),
    ]
    if include_unknown:
        trades.append(
            TradeRecord(
                code="688001", name="科创测试", op_type=OpType.UNKNOWN, qty=0.0,
                price=0.0, amount=0.0, balance=173277.84, fee=0.0,
                stamp_tax=0.0, commission=0.0, transfer_fee=0.0,
                contract_no="", trade_date=date(2026, 1, 14), currency="人民币",
            )
        )
    return trades


_NUMERIC_COLUMNS = {3, 4, 5, 6, 7, 8, 9, 10, 11, 15, 16, 17}
_DATE_COLUMN = 13


def _fmt(value: float) -> str:
    return format(float(value), ".15g")


def _trade_rows(trades: list[TradeRecord]) -> list[list[str]]:
    rows: list[list[str]] = []
    prev_balance = 0.0
    stock_balance: dict[str, float] = {}
    for trade in trades:
        code = trade.code
        if trade.op_type is OpType.BUY:
            stock_balance[code] = stock_balance.get(code, 0.0) + trade.qty
        elif trade.op_type is OpType.SELL:
            stock_balance[code] = max(0.0, stock_balance.get(code, 0.0) - trade.qty)
        elif trade.op_type is OpType.BONUS_SHARE:
            stock_balance[code] = stock_balance.get(code, 0.0) + trade.qty
        rows.append(
            [
                code,
                trade.name,
                OP_LABELS[trade.op_type],
                _fmt(trade.qty),
                _fmt(trade.price),
                _fmt(trade.amount),
                _fmt(stock_balance.get(code, 0.0)),
                _fmt(trade.balance - prev_balance),
                _fmt(trade.fee),
                _fmt(trade.stamp_tax),
                "0",
                _fmt(trade.balance),
                trade.contract_no,
                trade.trade_date.strftime("%Y%m%d") if trade.trade_date else "",
                trade.name,
                _fmt(trade.commission),
                _fmt(trade.transfer_fee),
                "0",
                trade.currency,
            ]
        )
        prev_balance = trade.balance
    return rows


def build_xlsx(path: str | Path, trades: list[TradeRecord] | None = None) -> Path:
    import openpyxl

    if trades is None:
        trades = make_trades()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "交割单"
    sheet.append(STANDARD_HEADERS)
    for row in _trade_rows(trades):
        cells = []
        for index, value in enumerate(row):
            if index in _NUMERIC_COLUMNS:
                cells.append(float(value) if value != "" else None)
            elif index == _DATE_COLUMN:
                cells.append(int(value) if value else None)
            else:
                cells.append(value or None)
        sheet.append(cells)
    workbook.save(target)
    return target


def build_csv(
    path: str | Path,
    trades: list[TradeRecord] | None = None,
    encoding: str = "utf-8-sig",
    delimiter: str = ",",
) -> Path:
    import csv as csv_module

    if trades is None:
        trades = make_trades()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding=encoding, newline="") as handle:
        writer = csv_module.writer(handle, delimiter=delimiter, lineterminator="\n")
        writer.writerow(STANDARD_HEADERS)
        for row in _trade_rows(trades):
            writer.writerow(row)
    return target


def build_txt(
    path: str | Path,
    trades: list[TradeRecord] | None = None,
    encoding: str = "utf-8",
    delimiter: str = "\t",
) -> Path:
    if trades is None:
        trades = make_trades()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [delimiter.join(STANDARD_HEADERS)]
    lines.extend(delimiter.join(row) for row in _trade_rows(trades))
    target.write_bytes(("\n".join(lines) + "\n").encode(encoding))
    return target


def make_zhangle_trades(include_unknown: bool = False) -> list[TradeRecord]:
    trades: list[TradeRecord] = []
    prev_balance = 0.0
    for trade in make_trades(include_unknown=include_unknown):
        cash_amount = trade.balance - prev_balance
        trades.append(
            TradeRecord(
                code=trade.code,
                name="",
                op_type=trade.op_type,
                qty=trade.qty,
                price=trade.price,
                amount=trade.amount,
                balance=0.0,
                cash_amount=cash_amount,
                fee=trade.fee,
                stamp_tax=trade.stamp_tax,
                commission=0.0,
                transfer_fee=trade.transfer_fee,
                contract_no=trade.contract_no,
                trade_date=trade.trade_date,
                currency="",
            )
        )
        prev_balance = trade.balance
    return trades


def build_zhangle_csv(
    path: str | Path,
    trades: list[TradeRecord] | None = None,
    encoding: str = "utf-8-sig",
) -> Path:
    import csv as csv_module

    if trades is None:
        trades = make_zhangle_trades()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding=encoding, newline="") as handle:
        writer = csv_module.writer(handle, delimiter=",", lineterminator="\n")
        writer.writerow(ZHANGLE_HEADERS)
        for trade in trades:
            signed_qty = (
                -abs(trade.qty) if trade.op_type is OpType.SELL else abs(trade.qty)
            )
            writer.writerow(
                [
                    trade.trade_date.strftime("%Y%m%d") if trade.trade_date else "",
                    OP_LABELS[trade.op_type],
                    _fmt(trade.cash_amount),
                    trade.contract_no,
                    _fmt(trade.transfer_fee),
                    "0",
                    _fmt(trade.fee),
                    _fmt(trade.stamp_tax),
                    _fmt(trade.amount),
                    trade.code,
                    _fmt(signed_qty),
                    _fmt(trade.price),
                ]
            )
    return target


def _write_workbook(
    path: Path, headers: list[str], rows: list[list], title: str | None = None
) -> Path:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    if title is not None:
        sheet.append([title])
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


def _assert_trades_equal(got: list[TradeRecord], expected: list[TradeRecord]) -> None:
    assert len(got) == len(expected)
    for actual, exp in zip(got, expected):
        assert actual.code == exp.code
        assert actual.name == exp.name
        assert actual.op_type is exp.op_type
        assert actual.trade_date == exp.trade_date
        assert actual.currency == exp.currency
        assert actual.contract_no == exp.contract_no
        assert actual.qty == pytest.approx(exp.qty)
        assert actual.price == pytest.approx(exp.price)
        assert actual.amount == pytest.approx(exp.amount)
        assert actual.balance == pytest.approx(exp.balance)
        assert actual.cash_amount == pytest.approx(exp.cash_amount)
        assert actual.fee == pytest.approx(exp.fee)
        assert actual.stamp_tax == pytest.approx(exp.stamp_tax)
        assert actual.commission == pytest.approx(exp.commission)
        assert actual.transfer_fee == pytest.approx(exp.transfer_fee)


# ---------------------------------------------------------------------------
# 夹具自检
# ---------------------------------------------------------------------------


def test_make_trades_covers_ten_op_types():
    trades = make_trades()
    assert {t.op_type for t in trades} == REAL_TEN_OPS
    assert len(trades) >= 10
    for trade in trades:
        assert isinstance(trade, TradeRecord)
        assert isinstance(trade.to_dict(), dict)


def test_make_trades_midstream_start_scenario():
    trades = make_trades()
    assert trades[0].op_type is OpType.SELL
    assert trades[0].code == "600519"
    assert trades[1].op_type is OpType.BANK_TO_SEC


# ---------------------------------------------------------------------------
# 合成 xlsx 往返
# ---------------------------------------------------------------------------


def test_parse_synthetic_xlsx_roundtrip(tmp_path):
    path = build_xlsx(tmp_path / "synthetic.xlsx")
    trades = parse_trades(path)
    _assert_trades_equal(trades, make_trades())
    assert {t.op_type for t in trades} == REAL_TEN_OPS


def test_parse_shuffled_columns_with_spaces_and_title(tmp_path):
    """列序打乱、表头带前导空格、首行是标题行、缺可选列（默认 0/空）。"""
    path = _write_workbook(
        tmp_path / "shuffled.xlsx",
        headers=[
            "资金余额",
            " 币种",
            "证券代码",
            "成交数量",
            "操作",
            "交收日期",
            "成交金额",
            "成交均价",
            "手续费",
            "印花税",
            "合同编号",
            "佣金",
            "证券中文全称",
        ],
        rows=[
            [
                100.0,
                "人民币",
                7.0,
                100.0,
                "证券买入",
                20260105,
                1000.0,
                10.0,
                5.0,
                0.0,
                "AD12345678",
                5.0,
                "测试股份",
            ]
        ],
        title="XX证券 客户交割单 2026",
    )
    trades = parse_trades(path)
    assert len(trades) == 1
    trade = trades[0]
    assert trade.code == "7"
    assert trade.name == "测试股份"
    assert trade.op_type is OpType.BUY
    assert trade.trade_date == date(2026, 1, 5)
    assert trade.currency == "人民币"
    assert trade.balance == pytest.approx(100.0)
    assert trade.fee == pytest.approx(5.0)
    assert trade.contract_no == "AD12345678"


def test_fee_and_transfer_fee_aggregation(tmp_path):
    """手续费+其他杂费 -> fee；过户费+清算费(B股) -> transfer_fee。"""
    path = _write_workbook(
        tmp_path / "fees.xlsx",
        headers=STANDARD_HEADERS,
        rows=[
            [
                "000002", "万科A", "证券买入", 100.0, 10.0, 1000.0, 100.0,
                -1005.5, 5.0, 0.0, 0.5, 8994.5, "AD00000099", 20260106,
                "万科A", 5.0, 1.0, 0.2, "人民币",
            ]
        ],
    )
    (trade,) = parse_trades(path)
    assert trade.fee == pytest.approx(5.5)
    assert trade.transfer_fee == pytest.approx(1.2)
    assert trade.commission == pytest.approx(5.0)


def test_unknown_op_does_not_raise(tmp_path):
    path = _write_workbook(
        tmp_path / "unknown.xlsx",
        headers=STANDARD_HEADERS,
        rows=[
            [
                "688001", "科创测试", "新股申购", 100.0, 20.0, 2000.0, 100.0,
                -2005.0, 5.0, 0.0, 0.0, 5000.0, "AD00000088", 20251128,
                "科创测试", 5.0, 0.0, 0.0, "人民币",
            ]
        ],
    )
    trades = parse_trades(path)
    assert len(trades) == 1
    assert trades[0].op_type is OpType.UNKNOWN


def test_blank_and_no_op_rows_skipped(tmp_path):
    path = _write_workbook(
        tmp_path / "blank.xlsx",
        headers=STANDARD_HEADERS,
        rows=[
            [
                "000001", "平安银行", "证券买入", 100.0, 10.0, 1000.0, 100.0,
                -1005.0, 5.0, 0.0, 0.0, 5000.0, "AD00000001", 20251127,
                "平安银行", 5.0, 0.0, 0.0, "人民币",
            ],
            [None] * 19,
            [
                "000002", "万科A", None, 200.0, 8.0, 1600.0, 200.0, -1605.0,
                5.0, 0.0, 0.0, 3395.0, "AD00000002", 20251128, "万科A",
                5.0, 0.0, 0.0, "人民币",
            ],
        ],
    )
    trades = parse_trades(path)
    assert len(trades) == 1
    assert trades[0].code == "000001"


def test_zero_amount_rows_preserved(tmp_path):
    path = build_xlsx(tmp_path / "zero.xlsx")
    trades = parse_trades(path)
    bank = next(t for t in trades if t.op_type is OpType.BANK_TO_SEC)
    assert bank.amount == 0.0
    assert bank.qty == 0.0
    assert bank.balance == pytest.approx(199818.50)


# ---------------------------------------------------------------------------
# 日期兼容
# ---------------------------------------------------------------------------


def test_date_formats_int_str_datetime_excel_serial(tmp_path):
    path = _write_workbook(
        tmp_path / "dates.xlsx",
        headers=STANDARD_HEADERS,
        rows=[
            ["000001", "平安银行", "证券买入", 100.0, 10.0, 1000.0, 100.0,
             -1005.0, 5.0, 0.0, 0.0, 5000.0, "AD00000001", 20251127,
             "平安银行", 5.0, 0.0, 0.0, "人民币"],
            ["000001", "平安银行", "证券买入", 100.0, 10.0, 1000.0, 100.0,
             -1005.0, 5.0, 0.0, 0.0, 5000.0, "AD00000001", "2025-11-28",
             "平安银行", 5.0, 0.0, 0.0, "人民币"],
            ["000001", "平安银行", "证券买入", 100.0, 10.0, 1000.0, 100.0,
             -1005.0, 5.0, 0.0, 0.0, 5000.0, "AD00000001",
             datetime(2025, 11, 29, 10, 30, 0), "平安银行", 5.0, 0.0, 0.0,
             "人民币"],
            ["000001", "平安银行", "证券买入", 100.0, 10.0, 1000.0, 100.0,
             -1005.0, 5.0, 0.0, 0.0, 5000.0, "AD00000001", "2025/11/30",
             "平安银行", 5.0, 0.0, 0.0, "人民币"],
            ["000001", "平安银行", "证券买入", 100.0, 10.0, 1000.0, 100.0,
             -1005.0, 5.0, 0.0, 0.0, 5000.0, "AD00000001", 46023,
             "平安银行", 5.0, 0.0, 0.0, "人民币"],
        ],
    )
    trades = parse_trades(path)
    assert [t.trade_date for t in trades] == [
        date(2025, 11, 27),
        date(2025, 11, 28),
        date(2025, 11, 29),
        date(2025, 11, 30),
        date(2026, 1, 1),
    ]


# ---------------------------------------------------------------------------
# CSV / TXT
# ---------------------------------------------------------------------------

_CSV_HEADERS = ",".join(STANDARD_HEADERS)
_CSV_ROW = (
    "000001,平安银行,证券买入,100,10.5,\"1,050.00\",100,-1055,5,0,0,"
    "4945,AD12345678,2025-11-27,平安银行,5,0,0,人民币"
)


def test_parse_csv_utf8_sig_and_gbk(tmp_path):
    for index, encoding in enumerate(("utf-8-sig", "gbk")):
        path = tmp_path / f"sample_{index}.csv"
        path.write_bytes((_CSV_HEADERS + "\n" + _CSV_ROW + "\n").encode(encoding))
        trades = parse_trades(path)
        assert len(trades) == 1
        trade = trades[0]
        assert trade.code == "000001"
        assert trade.name == "平安银行"
        assert trade.op_type is OpType.BUY
        assert trade.trade_date == date(2025, 11, 27)
        assert trade.amount == pytest.approx(1050.0)
        assert trade.balance == pytest.approx(4945.0)


def test_parse_csv_tab_delimited(tmp_path):
    path = build_csv(tmp_path / "tab_delimited.csv", delimiter="\t")
    trades = parse_trades(path)
    _assert_trades_equal(trades, make_trades())


def test_parse_csv_semicolon_delimited(tmp_path):
    path = build_csv(tmp_path / "semicolon.csv", delimiter=";")
    trades = parse_trades(path)
    assert len(trades) == len(make_trades())
    assert trades[0].op_type is OpType.SELL
    assert trades[0].code == "600519"
    assert trades[0].balance == pytest.approx(149818.50)


def test_parse_csv_gbk_tab_delimited(tmp_path):
    path = build_csv(tmp_path / "gbk_tab.csv", encoding="gbk", delimiter="\t")
    trades = parse_trades(path)
    assert len(trades) == len(make_trades())
    assert trades[0].name == "贵州茅台"
    assert trades[0].trade_date == date(2025, 11, 27)


def test_parse_txt_tab_delimited(tmp_path):
    path = build_txt(tmp_path / "tab.txt", delimiter="\t")
    trades = parse_trades(path)
    _assert_trades_equal(trades, make_trades())


def test_parse_txt_space_delimited(tmp_path):
    path = build_txt(tmp_path / "space.txt", delimiter=" ")
    trades = parse_trades(path)
    _assert_trades_equal(trades, make_trades())


def test_parse_txt_unknown_delimiter_raises(tmp_path):
    path = tmp_path / "unknown.txt"
    path.write_text("这是一行没有分隔符的内容\n第二行也没有分隔符\n", encoding="utf-8")
    with pytest.raises(ParseError) as excinfo:
        parse_trades(path)
    assert "分隔符" in str(excinfo.value)


def test_parse_text_undecodable_raises(tmp_path):
    path = tmp_path / "bad.txt"
    path.write_bytes(b"\xff\xff\xff\xff")
    with pytest.raises(ParseError) as excinfo:
        parse_trades(path)
    assert "编码" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 涨乐财富通版式
# ---------------------------------------------------------------------------


def test_make_zhangle_trades_layout():
    trades = make_zhangle_trades()
    assert {t.op_type for t in trades} == REAL_TEN_OPS
    assert all(t.name == "" for t in trades)
    assert all(t.balance == 0.0 for t in trades)
    assert all(t.currency == "" for t in trades)
    assert all(t.commission == 0.0 for t in trades)
    buy = next(t for t in trades if t.op_type is OpType.BUY)
    sell = next(t for t in trades if t.op_type is OpType.SELL)
    assert buy.cash_amount < 0
    assert sell.cash_amount > 0


def test_parse_zhangle_csv_roundtrip(tmp_path):
    path = build_zhangle_csv(tmp_path / "zhangle.csv")
    trades = parse_trades(path)
    _assert_trades_equal(trades, make_zhangle_trades())
    assert {t.op_type for t in trades} == REAL_TEN_OPS
    assert all(t.name == "" and t.balance == 0.0 and t.currency == "" for t in trades)


def test_parse_zhangle_qty_sign_trusts_summary(tmp_path):
    """数量符号与摘要不一致时以摘要为准，数量取绝对值；费用/现金流水映射。"""
    path = tmp_path / "zhangle_raw.csv"
    path.write_bytes(
        (
            "日期,摘要,发生金额,委托号,过户费,其他杂费,手续费/佣金,印花税,"
            "成交金额,股票代码,成交数量,成交价格\n"
            "20260630,证券买入,-2541.53,144152,0.03,0.50,5.00,0.00,"
            "2536.00,000630,-400.00,6.340\n"
            "20260630,证券卖出,2569.63,115458,0.00,0.00,0.77,0.00,"
            "2570.40,159559,1800.00,1.428\n"
        ).encode("utf-8")
    )
    trades = parse_trades(path)
    assert len(trades) == 2
    buy, sell = trades
    assert buy.op_type is OpType.BUY
    assert buy.qty == pytest.approx(400.0)
    assert sell.op_type is OpType.SELL
    assert sell.qty == pytest.approx(1800.0)
    assert buy.code == "000630"
    assert buy.name == ""
    assert buy.trade_date == date(2026, 6, 30)
    assert buy.amount == pytest.approx(2536.0)
    assert buy.price == pytest.approx(6.34)
    assert buy.fee == pytest.approx(5.5)
    assert buy.stamp_tax == pytest.approx(0.0)
    assert buy.transfer_fee == pytest.approx(0.03)
    assert buy.cash_amount == pytest.approx(-2541.53)
    assert buy.balance == 0.0
    assert buy.contract_no == "144152"
    assert buy.to_dict()["contract_no"] == "**4152"
    assert sell.cash_amount == pytest.approx(2569.63)
    assert sell.fee == pytest.approx(0.77)


def test_parse_zhangle_gbk_encoding(tmp_path):
    path = build_zhangle_csv(tmp_path / "zhangle_gbk.csv", encoding="gbk")
    trades = parse_trades(path)
    assert len(trades) == len(make_zhangle_trades())
    assert trades[0].code == "600519"
    assert trades[0].op_type is OpType.SELL
    assert trades[0].cash_amount == pytest.approx(149818.50)


# ---------------------------------------------------------------------------
# 错误处理（中文信息）
# ---------------------------------------------------------------------------


def test_missing_file_raises_chinese_error(tmp_path):
    with pytest.raises(ParseError) as excinfo:
        parse_trades(tmp_path / "不存在.xlsx")
    assert "文件不存在" in str(excinfo.value)


def test_directory_raises_chinese_error(tmp_path):
    with pytest.raises(ParseError) as excinfo:
        parse_trades(tmp_path)
    assert "目录" in str(excinfo.value)


def test_unparseable_file_raises_chinese_error(tmp_path):
    path = tmp_path / "bad.xlsx"
    path.write_bytes(b"this is not a real xlsx file")
    with pytest.raises(ParseError) as excinfo:
        parse_trades(path)
    assert "无法解析" in str(excinfo.value)


def test_missing_required_column_raises_chinese_error(tmp_path):
    path = _write_workbook(
        tmp_path / "no_op.xlsx",
        headers=["证券代码", "证券名称", "成交数量", "成交均价"],
        rows=[["000001", "平安银行", 100.0, 10.0]],
    )
    with pytest.raises(ParseError) as excinfo:
        parse_trades(path)
    assert "操作" in str(excinfo.value)
    assert "交收日期" in str(excinfo.value)


def test_empty_workbook_raises_chinese_error(tmp_path):
    path = _write_workbook(tmp_path / "empty.xlsx", headers=["随便"], rows=[])
    with pytest.raises(ParseError) as excinfo:
        parse_trades(path)
    assert "表头" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 序列化与脱敏
# ---------------------------------------------------------------------------


def test_to_dict_masks_contract_no_and_is_json_serializable():
    trade = make_trades()[0]
    assert trade.contract_no == "AD00000001"
    payload = trade.to_dict()
    assert payload["contract_no"] == "******0001"
    assert payload["op_type"] == "SELL"
    assert payload["trade_date"] == "2025-11-27"
    json.dumps(payload, ensure_ascii=False)
    assert make_trades()[1].to_dict()["contract_no"] == ""


def test_op_labels_cover_all_enum_members():
    assert len(OP_LABELS) == len(OpType)
    for op in OpType:
        assert op in OP_LABELS


# ---------------------------------------------------------------------------
# 操作别名
# ---------------------------------------------------------------------------


def test_op_contains_matching_variants(tmp_path):
    path = _write_workbook(
        tmp_path / "variants.xlsx",
        headers=STANDARD_HEADERS,
        rows=[
            ["131810", "Ｒ-001", "逆回购到期", 10.0, 100.0, 1000.0, 0.0,
             1000.0, 0.0, 0.0, 0.0, 6000.0, "AD00000011", 20260108,
             "Ｒ-001", 0.0, 0.0, 0.0, "人民币"],
            ["000001", "平安银行", "证券买入(信用)", 100.0, 10.0, 1000.0,
             100.0, -1005.0, 5.0, 0.0, 0.0, 4995.0, "AD00000012",
             20260109, "平安银行", 5.0, 0.0, 0.0, "人民币"],
        ],
    )
    trades = parse_trades(path)
    assert [t.op_type for t in trades] == [OpType.REPO, OpType.BUY]


def test_header_only_workbook_returns_empty_list(tmp_path):
    path = _write_workbook(
        tmp_path / "headers_only.xlsx", headers=STANDARD_HEADERS, rows=[]
    )
    assert parse_trades(path) == []
