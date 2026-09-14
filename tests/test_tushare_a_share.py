from __future__ import annotations

import json
import os

import pytest
import yaml

from market_data_platform.cli import build_parser
from market_data_platform.providers import tushare_a_share
from market_data_platform.providers._client import _TushareEndpointFailover
from market_data_platform.providers._env import resolve_tushare_api_urls
from market_data_platform.tushare_backfill import (
    AShareHistoryBackfillOptions,
    build_a_share_backfill_plan,
    run_a_share_history_backfill,
)
from market_data_platform.tushare_refresh import (
    _replace_latest_symlink,
    build_a_share_current_refresh_plan,
    run_a_share_current_promotion,
)


class FakeDataClient:
    def __init__(self, pd) -> None:
        self.pd = pd
        self._DataApi__http_url: str = ""
        self.daily_dates: list[str] = []
        self.daily_fields: list[str] = []
        self.fund_daily_dates: list[str] = []
        self.fund_daily_fields: list[str] = []
        self.fund_adj_dates: list[str] = []
        self.fund_adj_fields: list[str] = []
        self.daily_basic_fields: list[str] = []
        self.moneyflow_fields: list[str] = []
        self.moneyflow_dc_fields: list[str] = []
        self.moneyflow_hsgt_fields: list[str] = []
        self.top_inst_fields: list[str] = []
        self.top_inst_dates: list[str] = []
        self.fund_portfolio_fields: list[str] = []
        self.fund_portfolio_periods: list[str] = []
        self.fund_portfolio_offsets: list[int] = []
        self.top10_holders_fields: list[str] = []
        self.top10_holders_symbols: list[str] = []
        self.top10_floatholders_fields: list[str] = []
        self.top10_floatholders_symbols: list[str] = []
        self.stk_holdertrade_fields: list[str] = []
        self.stk_holdertrade_symbols: list[str] = []
        self.query_calls: list[tuple[str, dict[str, object]]] = []
        self.proxy_snapshots: list[dict[str, str | None]] = []

    def _record_proxy_env(self) -> None:
        self.proxy_snapshots.append(
            {
                key: os.environ.get(key)
                for key in (
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "ALL_PROXY",
                    "http_proxy",
                    "https_proxy",
                    "all_proxy",
                    "NO_PROXY",
                    "no_proxy",
                )
            }
        )

    def stock_basic(self, *, exchange: str, list_status: str, fields: str):
        self._record_proxy_env()
        assert exchange == ""
        assert "ts_code" in fields
        return self.pd.DataFrame(
            {
                "ts_code": [f"00000{1 if list_status == 'L' else 2}.SZ"],
                "list_status": [list_status],
                "name": ["demo"],
                "market": ["Main Board"],
            }
        )

    def trade_cal(self, **kwargs):
        self._record_proxy_env()
        return self.pd.DataFrame(
            {
                "cal_date": ["20260522", "20260523", "20260525"],
                "is_open": ["1", "0", "1"],
            }
        )

    def daily(self, *, trade_date: str, **kwargs):
        self._record_proxy_env()
        self.daily_dates.append(trade_date)
        self.daily_fields.append(kwargs.get("fields", ""))
        return self.pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [trade_date],
                "open": [10.0],
                "close": [10.1],
            }
        )

    def daily_basic(self, *, trade_date: str, **kwargs):
        self._record_proxy_env()
        self.daily_basic_fields.append(kwargs.get("fields", ""))
        return self.pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [trade_date],
                "close": [10.1],
                "total_mv": [100.0],
            }
        )

    def fund_daily(self, *, trade_date: str, **kwargs):
        self._record_proxy_env()
        self.fund_daily_dates.append(trade_date)
        self.fund_daily_fields.append(kwargs.get("fields", ""))
        return self.pd.DataFrame(
            {
                "ts_code": ["510300.SH"],
                "trade_date": [trade_date],
                "open": [4.0],
                "high": [4.1],
                "low": [3.9],
                "close": [4.05],
                "pre_close": [4.0],
                "vol": [1000.0],
                "amount": [4050.0],
            }
        )

    def fund_adj(self, *, trade_date: str, **kwargs):
        self._record_proxy_env()
        self.fund_adj_dates.append(trade_date)
        self.fund_adj_fields.append(kwargs.get("fields", ""))
        return self.pd.DataFrame(
            {
                "ts_code": ["510300.SH"],
                "trade_date": [trade_date],
                "adj_factor": [1.0],
            }
        )

    def moneyflow(self, *, trade_date: str, **kwargs):
        self._record_proxy_env()
        self.moneyflow_fields.append(kwargs.get("fields", ""))
        return self.pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [trade_date],
                "buy_elg_amount": [20.0],
                "sell_elg_amount": [8.0],
                "buy_lg_amount": [15.0],
                "sell_lg_amount": [6.0],
                "net_mf_amount": [12.0],
            }
        )

    def moneyflow_dc(self, *, trade_date: str, **kwargs):
        self._record_proxy_env()
        self.moneyflow_dc_fields.append(kwargs.get("fields", ""))
        return self.pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [trade_date],
                "net_amount": [12.0],
                "buy_elg_amount": [20.0],
                "buy_lg_amount": [15.0],
                "buy_md_amount": [5.0],
                "buy_sm_amount": [-3.0],
            }
        )

    def moneyflow_hsgt(self, *, trade_date: str, **kwargs):
        self._record_proxy_env()
        self.moneyflow_hsgt_fields.append(kwargs.get("fields", ""))
        return self.pd.DataFrame(
            {
                "trade_date": [trade_date],
                "hgt": [10.0],
                "sgt": [5.0],
                "ggt_ss": [3.0],
                "ggt_sz": [2.0],
                "north_money": [15.0],
                "south_money": [5.0],
            }
        )

    def top_inst(self, *, trade_date: str, **kwargs):
        self._record_proxy_env()
        self.top_inst_dates.append(trade_date)
        self.top_inst_fields.append(kwargs.get("fields", ""))
        return self.pd.DataFrame(
            {
                "trade_date": [trade_date],
                "ts_code": ["000001.SZ"],
                "exalter": ["机构专用"],
                "buy": [100.0],
                "buy_rate": [10.0],
                "sell": [40.0],
                "sell_rate": [4.0],
                "net_buy": [60.0],
            }
        )

    def fund_portfolio(self, *, period: str, **kwargs):
        self._record_proxy_env()
        self.fund_portfolio_periods.append(period)
        self.fund_portfolio_fields.append(kwargs.get("fields", ""))
        self.fund_portfolio_offsets.append(int(kwargs.get("offset", 0)))
        if int(kwargs.get("offset", 0)) > 0:
            return self.pd.DataFrame()
        return self.pd.DataFrame(
            {
                "ts_code": ["001753.OF"],
                "ann_date": ["20260420"],
                "end_date": [period],
                "symbol": ["600519.SH"],
                "mkv": [1000000.0],
                "amount": [10000.0],
                "stk_mkv_ratio": [1.2],
                "stk_float_ratio": [0.1],
            }
        )

    def top10_holders(self, *, ts_code: str, **kwargs):
        self._record_proxy_env()
        self.top10_holders_symbols.append(ts_code)
        self.top10_holders_fields.append(kwargs.get("fields", ""))
        return self.pd.DataFrame(
            {
                "ts_code": [ts_code, ts_code],
                "ann_date": ["20260420", "20260420"],
                "end_date": ["20260331", "20260331"],
                "holder_name": ["中国证券金融股份有限公司", "张三"],
                "hold_amount": [10000.0, 2000.0],
                "hold_ratio": [5.0, 1.0],
                "hold_float_ratio": [6.0, 1.2],
                "hold_change": [100.0, -20.0],
                "holder_type": ["机构", "个人"],
            }
        )

    def top10_floatholders(self, *, ts_code: str, **kwargs):
        self._record_proxy_env()
        self.top10_floatholders_symbols.append(ts_code)
        self.top10_floatholders_fields.append(kwargs.get("fields", ""))
        return self.pd.DataFrame(
            {
                "ts_code": [ts_code],
                "ann_date": ["20260420"],
                "end_date": ["20260331"],
                "holder_name": ["中央汇金资产管理有限责任公司"],
                "hold_amount": [8000.0],
                "hold_ratio": [4.0],
                "hold_float_ratio": [4.8],
                "hold_change": [0.0],
                "holder_type": ["机构"],
            }
        )

    def stk_holdertrade(self, *, ts_code: str, **kwargs):
        self._record_proxy_env()
        self.stk_holdertrade_symbols.append(ts_code)
        self.stk_holdertrade_fields.append(kwargs.get("fields", ""))
        return self.pd.DataFrame(
            {
                "ts_code": [ts_code],
                "ann_date": ["20260420"],
                "holder_name": ["控股股东"],
                "holder_type": ["控股股东"],
                "in_de": ["增持"],
                "change_vol": [10000.0],
                "change_ratio": [0.1],
                "after_share": [20000.0],
                "after_ratio": [0.2],
                "avg_price": [20.0],
                "total_share": [100000.0],
                "begin_date": ["20260401"],
                "close_date": ["20260420"],
            }
        )

    def query(self, api_name: str, **kwargs):  # noqa: PLR0911
        self._record_proxy_env()
        self.query_calls.append((api_name, dict(kwargs)))
        date = str(kwargs.get("trade_date") or kwargs.get("start_date") or "")
        if api_name == "ths_hot":
            return self.pd.DataFrame(
                {
                    "trade_date": [date],
                    "data_type": ["热股"],
                    "ts_code": ["000001.SZ"],
                    "ts_name": ["平安银行"],
                    "rank": [1],
                    "pct_change": [2.0],
                    "current_price": [10.5],
                    "hot": [100.0],
                    "concept": ["银行"],
                }
            )
        if api_name == "dc_concept":
            return self.pd.DataFrame(
                {
                    "theme_code": ["BK0001"],
                    "trade_date": [date],
                    "name": ["AI"],
                    "strength": [80.0],
                    "z_t_num": [3],
                }
            )
        if api_name == "kpl_list":
            return self.pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "name": ["平安银行"],
                    "trade_date": [date],
                    "tag": [kwargs.get("tag", "涨停")],
                    "theme": ["金融"],
                    "pct_chg": [10.0],
                }
            )
        if api_name == "limit_step":
            return self.pd.DataFrame(
                {"ts_code": ["000001.SZ"], "name": ["平安银行"], "trade_date": [date], "nums": [2]}
            )
        if api_name == "limit_cpt_list":
            return self.pd.DataFrame(
                {
                    "ts_code": ["BK0001"],
                    "name": ["AI"],
                    "trade_date": [date],
                    "up_nums": [5],
                    "rank": [1],
                }
            )
        if api_name == "report_rc":
            return self.pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "name": ["平安银行"],
                    "report_date": [date],
                    "report_title": ["盈利预测上调"],
                    "org_name": ["券商"],
                    "rating": ["买入"],
                }
            )
        if api_name == "stk_surv":
            return self.pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "name": ["平安银行"],
                    "surv_date": [date],
                    "fund_visitors": ["基金A"],
                    "rece_mode": ["现场"],
                }
            )
        if api_name == "broker_recommend":
            return self.pd.DataFrame(
                {
                    "month": [kwargs["month"]],
                    "broker": ["券商"],
                    "ts_code": ["000001.SZ"],
                    "name": ["平安银行"],
                }
            )
        return self.pd.DataFrame()


class FakeTushare:
    def __init__(self) -> None:
        self.tokens: list[str] = []
        self._DataApi__http_url: str = ""

    def pro_api(self, *, token: str):
        self.tokens.append(token)
        return self

    def trade_cal(self, *, exchange: str, start_date: str, end_date: str):
        assert exchange == ""
        assert start_date == "20200101"
        assert end_date == "20200110"
        return []


def test_verify_tokens_reports_status_without_exposing_token(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "secret-primary-token")
    monkeypatch.delenv("TUSHARE_TOKEN_2", raising=False)
    monkeypatch.setenv("TUSHARE_API_URL", "")

    summary = tushare_a_share.verify_tushare_tokens(
        env_keys=["TUSHARE_TOKEN"],
        tushare_module=FakeTushare(),
    )

    assert summary["valid_tokens"] == 1
    assert summary["results"][0] == {
        "env": "TUSHARE_TOKEN",
        "configured": True,
        "valid": True,
        "api_url": None,
    }
    assert "secret-primary-token" not in json.dumps(summary)


def test_verify_tokens_loads_local_env_file_without_overriding_shell_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setenv("TUSHARE_TOKEN_2", "shell-token")
    (tmp_path / ".env.local").write_text(
        "TUSHARE_TOKEN=local-token\nTUSHARE_TOKEN_2=local-ignored-token\n",
        encoding="utf-8",
    )
    fake_tushare = FakeTushare()

    summary = tushare_a_share.verify_tushare_tokens(
        env_keys=["TUSHARE_TOKEN", "TUSHARE_TOKEN_2"],
        tushare_module=fake_tushare,
    )

    assert summary["valid_tokens"] == 2
    assert fake_tushare.tokens == ["local-token", "shell-token"]


def test_verify_tokens_uses_token_specific_api_url_env(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setenv("TUSHARE_TOKEN_2", "secret-secondary-token")
    monkeypatch.setenv("TUSHARE_API_URL_2", "https://proxy-a.example.com/")
    fake_tushare = FakeTushare()

    summary = tushare_a_share.verify_tushare_tokens(
        env_keys=["TUSHARE_TOKEN_2"],
        tushare_module=fake_tushare,
    )

    assert summary["valid_tokens"] == 1
    assert summary["results"][0]["api_url"] == "https://proxy-a.example.com"
    assert fake_tushare._DataApi__http_url == "https://proxy-a.example.com"
    assert "secret-secondary-token" not in json.dumps(summary)


def test_verify_tokens_redacts_token_echoed_by_provider_error(monkeypatch):
    class RejectingTushare(FakeTushare):
        def trade_cal(self, *, exchange: str, start_date: str, end_date: str):
            raise RuntimeError(f"invalid token: {self.tokens[-1]}")

    monkeypatch.setenv("TUSHARE_TOKEN", "secret-primary-token")

    summary = tushare_a_share.verify_tushare_tokens(
        env_keys=["TUSHARE_TOKEN"],
        tushare_module=RejectingTushare(),
    )

    assert summary["valid_tokens"] == 0
    assert "secret-primary-token" not in json.dumps(summary)
    assert "<redacted>" in summary["results"][0]["error"]


def test_verify_token_command_exposes_proxy_flag():
    parser = build_parser()

    parsed = parser.parse_args(
        [
            "tushare",
            "verify-token",
            "--env",
            "TUSHARE_TOKEN_2",
            "--api-url",
            "https://proxy-a.example.com",
            "--use-proxy",
        ]
    )

    assert parsed.tushare_command == "verify-token"
    assert parsed.env_keys == ["TUSHARE_TOKEN_2"]
    assert parsed.api_url == "https://proxy-a.example.com"
    assert parsed.use_proxy is True


def test_export_a_share_instruments_writes_manifest_and_canonical_symbols(tmp_path):
    pd = pytest.importorskip("pandas")
    output = tmp_path / "a_share_instruments.csv"
    symbols_output = tmp_path / "symbols.txt"

    manifest = tushare_a_share.export_a_share_instruments(
        out=output,
        symbols_out=symbols_output,
        list_statuses=["L", "D"],
        client=FakeDataClient(pd),
    )

    exported = pd.read_csv(output)
    assert exported["symbol"].tolist() == ["000001.SZ", "000002.SZ"]
    assert exported["market"].tolist() == ["Main Board", "Main Board"]
    assert exported["platform_market"].tolist() == ["a_share", "a_share"]
    assert symbols_output.read_text(encoding="utf-8") == "000001.SZ\n000002.SZ\n"
    assert manifest["provider"] == "tushare"
    manifest_payload = yaml.safe_load((tmp_path / "a_share_instruments.manifest.yml").read_text())
    assert manifest_payload["dataset"] == "instruments"


def test_export_a_share_instruments_sets_explicit_api_url_on_client(tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)

    manifest = tushare_a_share.export_a_share_instruments(
        out=tmp_path / "a_share_instruments.csv",
        list_statuses=["L"],
        client=client,
        api_url="https://proxy-b.example.com/",
    )

    assert manifest["api_url"] == "https://proxy-b.example.com"
    assert client._DataApi__http_url == "https://proxy-b.example.com"


def test_daily_mirror_fetches_full_market_by_open_trade_date(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    manifest = tushare_a_share.mirror_a_share_daily(
        out_dir=tmp_path / "a_share_daily",
        start_date="20260522",
        end_date="20260525",
        client=client,
    )

    assert client.daily_dates == ["20260522", "20260525"]
    assert all(field_text is not None for field_text in client.daily_fields)
    assert "open" in client.daily_fields[0]
    assert "pct_chg" in client.daily_fields[0]
    assert "amount" in client.daily_fields[0]
    assert (tmp_path / "a_share_daily" / "data" / "trade_date=20260522" / "part.parquet").exists()
    assert manifest["totals"]["trade_dates_written"] == 2
    assert manifest["query"]["partition_by"] == "trade_date"
    assert manifest["query"]["fields"] == list(tushare_a_share.DEFAULT_DAILY_FIELDS)


def test_tushare_downloads_disable_proxy_env_by_default(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10810")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10810")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:10810")
    monkeypatch.setenv("NO_PROXY", "original.local")

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    manifest = tushare_a_share.mirror_a_share_daily(
        out_dir=tmp_path / "a_share_daily",
        start_date="20260522",
        end_date="20260525",
        client=client,
    )

    assert client.proxy_snapshots
    for snapshot in client.proxy_snapshots:
        assert snapshot["HTTP_PROXY"] is None
        assert snapshot["HTTPS_PROXY"] is None
        assert snapshot["ALL_PROXY"] is None
        assert snapshot["NO_PROXY"] == "*"
        assert snapshot["no_proxy"] == "*"
    assert os.environ["HTTP_PROXY"] == "http://127.0.0.1:10810"
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:10810"
    assert os.environ["ALL_PROXY"] == "http://127.0.0.1:10810"
    assert os.environ["NO_PROXY"] == "original.local"
    assert manifest["request_policy"]["disable_proxy"] is True


def test_tushare_downloads_allow_proxy_env_when_requested(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10810")

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    manifest = tushare_a_share.mirror_a_share_daily(
        out_dir=tmp_path / "a_share_daily",
        start_date="20260522",
        end_date="20260525",
        client=client,
        request_policy=tushare_a_share.TushareRequestPolicy(disable_proxy=False),
    )

    assert client.proxy_snapshots
    assert all(
        snapshot["HTTP_PROXY"] == "http://127.0.0.1:10810" for snapshot in client.proxy_snapshots
    )
    assert manifest["request_policy"]["disable_proxy"] is False


def test_tushare_download_retries_transient_timeout(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")

    class TimeoutThenSuccessClient(FakeDataClient):
        def __init__(self, pd) -> None:
            super().__init__(pd)
            self.daily_attempts = 0

        def daily(self, *, trade_date: str, **kwargs):
            self.daily_attempts += 1
            if self.daily_attempts == 1:
                raise RuntimeError("Read timed out")
            return super().daily(trade_date=trade_date, **kwargs)

    sleeps: list[float] = []
    client = TimeoutThenSuccessClient(pd)

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    monkeypatch.setattr(tushare_a_share.time, "sleep", sleeps.append)
    manifest = tushare_a_share.mirror_a_share_daily(
        out_dir=tmp_path / "a_share_daily",
        start_date="20260522",
        end_date="20260525",
        client=client,
        request_attempts=2,
        retry_sleep_seconds=0.5,
        retry_max_sleep_seconds=1.0,
    )

    assert client.daily_attempts == 3
    assert sleeps == [0.5]
    assert manifest["totals"]["trade_dates_written"] == 2


def test_tushare_download_uses_quota_cooldown_for_rate_limits(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")

    class QuotaThenSuccessClient(FakeDataClient):
        def __init__(self, pd) -> None:
            super().__init__(pd)
            self.daily_attempts = 0

        def daily(self, *, trade_date: str, **kwargs):
            self.daily_attempts += 1
            if self.daily_attempts == 1:
                raise RuntimeError("抱歉，您访问接口(daily)频率超限(500次/分钟)")
            return super().daily(trade_date=trade_date, **kwargs)

    sleeps: list[float] = []
    client = QuotaThenSuccessClient(pd)

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    monkeypatch.setattr(tushare_a_share.time, "sleep", sleeps.append)
    manifest = tushare_a_share.mirror_a_share_daily(
        out_dir=tmp_path / "a_share_daily",
        start_date="20260522",
        end_date="20260525",
        client=client,
        request_attempts=2,
        retry_sleep_seconds=0.5,
        quota_cooldown_seconds=7.0,
    )

    assert client.daily_attempts == 3
    assert sleeps == [7.0]
    assert manifest["totals"]["trade_dates_written"] == 2


def test_daily_basic_mirror_uses_default_fields(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    manifest = tushare_a_share.mirror_a_share_daily_basic(
        out_dir=tmp_path / "a_share_daily_basic",
        start_date="20260522",
        end_date="20260522",
        client=client,
    )

    assert client.daily_basic_fields
    assert "turnover_rate" in client.daily_basic_fields[0]
    assert "total_mv" in client.daily_basic_fields[0]
    assert "circ_mv" in client.daily_basic_fields[0]
    assert manifest["query"]["fields"] == list(tushare_a_share.DEFAULT_DAILY_BASIC_FIELDS)


def test_etf_daily_mirror_uses_fund_daily_and_command_is_exposed(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    manifest = tushare_a_share.mirror_etf_daily(
        out_dir=tmp_path / "etf_daily",
        start_date="20260522",
        end_date="20260525",
        client=client,
    )

    assert client.fund_daily_dates == ["20260522", "20260525"]
    assert client.fund_daily_fields
    assert "vol" in client.fund_daily_fields[0]
    assert "amount" in client.fund_daily_fields[0]
    assert (tmp_path / "etf_daily" / "data" / "trade_date=20260522" / "part.parquet").exists()
    assert manifest["schema_version"] == "tushare.fund_daily.v1"
    assert manifest["dataset"] == "fund_daily"
    assert manifest["market"] == "etf"
    assert manifest["query"]["fields"] == list(tushare_a_share.DEFAULT_FUND_DAILY_FIELDS)

    parser = build_parser()
    required = ["--out-dir", "output", "--start-date", "20260522", "--end-date", "20260525"]
    parsed = parser.parse_args(["tushare", "mirror-etf-daily", *required])
    assert parsed.tushare_command == "mirror-etf-daily"


def test_etf_adj_factor_mirror_uses_fund_adj_and_command_is_exposed(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    manifest = tushare_a_share.mirror_etf_adj_factor(
        out_dir=tmp_path / "etf_adj_factor",
        start_date="20260522",
        end_date="20260525",
        client=client,
    )

    assert client.fund_adj_dates == ["20260522", "20260525"]
    assert client.fund_adj_fields
    assert "adj_factor" in client.fund_adj_fields[0]
    assert manifest["schema_version"] == "tushare.fund_adj.v1"
    assert manifest["dataset"] == "fund_adj"
    assert manifest["market"] == "etf"
    assert manifest["query"]["fields"] == list(tushare_a_share.DEFAULT_FUND_ADJ_FIELDS)

    parser = build_parser()
    required = ["--out-dir", "output", "--start-date", "20260522", "--end-date", "20260525"]
    parsed = parser.parse_args(["tushare", "mirror-etf-adj-factor", *required])
    assert parsed.tushare_command == "mirror-etf-adj-factor"


def test_limit_status_command_is_exposed():
    parser = build_parser()
    required = ["--out-dir", "output", "--start-date", "20260522", "--end-date", "20260525"]

    parsed = parser.parse_args(["tushare", "mirror-a-share-limit-status", *required])

    assert parsed.tushare_command == "mirror-a-share-limit-status"


def test_moneyflow_mirror_uses_default_fields_and_command_is_exposed(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    manifest = tushare_a_share.mirror_a_share_moneyflow(
        out_dir=tmp_path / "a_share_moneyflow",
        start_date="20260522",
        end_date="20260525",
        client=client,
    )

    assert client.moneyflow_fields
    assert "buy_elg_amount" in client.moneyflow_fields[0]
    assert "net_mf_amount" in client.moneyflow_fields[0]
    assert manifest["schema_version"] == "tushare.moneyflow.v1"
    assert manifest["dataset"] == "moneyflow"
    assert manifest["query"]["fields"] == list(tushare_a_share.DEFAULT_MONEYFLOW_FIELDS)

    parser = build_parser()
    required = ["--out-dir", "output", "--start-date", "20260522", "--end-date", "20260525"]
    parsed = parser.parse_args(["tushare", "mirror-a-share-moneyflow", *required])
    assert parsed.tushare_command == "mirror-a-share-moneyflow"


def test_moneyflow_dc_mirror_uses_default_fields_and_command_is_exposed(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    manifest = tushare_a_share.mirror_a_share_moneyflow_dc(
        out_dir=tmp_path / "a_share_moneyflow_dc",
        start_date="20260522",
        end_date="20260525",
        client=client,
    )

    assert client.moneyflow_dc_fields
    assert "net_amount" in client.moneyflow_dc_fields[0]
    assert "buy_lg_amount" in client.moneyflow_dc_fields[0]
    assert manifest["schema_version"] == "tushare.moneyflow_dc.v1"
    assert manifest["dataset"] == "moneyflow_dc"
    assert manifest["query"]["fields"] == list(tushare_a_share.DEFAULT_MONEYFLOW_DC_FIELDS)

    parser = build_parser()
    required = ["--out-dir", "output", "--start-date", "20260522", "--end-date", "20260525"]
    parsed = parser.parse_args(["tushare", "mirror-a-share-moneyflow-dc", *required])
    assert parsed.tushare_command == "mirror-a-share-moneyflow-dc"


def test_moneyflow_hsgt_mirror_uses_default_fields_and_command_is_exposed(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    manifest = tushare_a_share.mirror_a_share_moneyflow_hsgt(
        out_dir=tmp_path / "a_share_moneyflow_hsgt",
        start_date="20260522",
        end_date="20260525",
        client=client,
    )

    assert client.moneyflow_hsgt_fields
    assert "trade_date" in client.moneyflow_hsgt_fields[0]
    assert "north_money" in client.moneyflow_hsgt_fields[0]
    assert "ts_code" not in client.moneyflow_hsgt_fields[0]
    assert manifest["schema_version"] == "tushare.moneyflow_hsgt.v1"
    assert manifest["dataset"] == "moneyflow_hsgt"
    assert manifest["query"]["fields"] == list(tushare_a_share.DEFAULT_MONEYFLOW_HSGT_FIELDS)

    parser = build_parser()
    required = ["--out-dir", "output", "--start-date", "20260522", "--end-date", "20260525"]
    parsed = parser.parse_args(["tushare", "mirror-a-share-moneyflow-hsgt", *required])
    assert parsed.tushare_command == "mirror-a-share-moneyflow-hsgt"


def test_top_inst_mirror_uses_default_fields_and_command_is_exposed(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    manifest = tushare_a_share.mirror_a_share_top_inst(
        out_dir=tmp_path / "a_share_top_inst",
        start_date="20260522",
        end_date="20260525",
        client=client,
    )

    assert client.top_inst_dates == ["20260522", "20260525"]
    assert client.top_inst_fields
    assert "exalter" in client.top_inst_fields[0]
    assert "net_buy" in client.top_inst_fields[0]
    assert manifest["schema_version"] == "tushare.top_inst.v1"
    assert manifest["dataset"] == "top_inst"
    assert manifest["query"]["fields"] == list(tushare_a_share.DEFAULT_TOP_INST_FIELDS)

    parser = build_parser()
    required = [
        "--out-dir",
        "output",
        "--start-date",
        "20260522",
        "--end-date",
        "20260525",
        "--request-interval-seconds",
        "0.5",
    ]
    parsed = parser.parse_args(["tushare", "mirror-a-share-top-inst", *required])
    assert parsed.tushare_command == "mirror-a-share-top-inst"
    assert parsed.request_interval_seconds == 0.5


def test_hotspot_trade_date_mirrors_use_query_fallback_and_are_exposed(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    manifest = tushare_a_share.mirror_a_share_ths_hot(
        out_dir=tmp_path / "ths_hot",
        start_date="20260522",
        end_date="20260525",
        client=client,
        query_options={"market": "热股", "is_new": "Y"},
    )

    assert [call[0] for call in client.query_calls[:2]] == ["ths_hot", "ths_hot"]
    assert client.query_calls[0][1]["market"] == "热股"
    assert client.query_calls[0][1]["is_new"] == "Y"
    assert "rank_reason" in str(client.query_calls[0][1]["fields"])
    assert manifest["schema_version"] == "tushare.ths_hot.v1"
    assert manifest["dataset"] == "ths_hot"
    assert manifest["query"]["query_options"] == {"market": "热股", "is_new": "Y"}

    client.query_calls.clear()
    kpl_manifest = tushare_a_share.mirror_a_share_kpl_list(
        out_dir=tmp_path / "kpl_list",
        start_date="20260522",
        end_date="20260522",
        client=client,
        query_options={"tag": ["涨停", "炸板"]},
    )
    assert {call[0] for call in client.query_calls} == {"kpl_list"}
    assert [call[1]["tag"] for call in client.query_calls[:2]] == ["涨停", "炸板"]
    assert kpl_manifest["dataset"] == "kpl_list"

    parser = build_parser()
    required = ["--out-dir", "output", "--start-date", "20260522", "--end-date", "20260525"]
    parsed = parser.parse_args(["tushare", "mirror-a-share-ths-hot", *required])
    assert parsed.tushare_command == "mirror-a-share-ths-hot"
    assert parsed.market == "热股"
    parsed = parser.parse_args(["tushare", "mirror-a-share-kpl-list", *required, "--tag", "炸板"])
    assert parsed.tushare_command == "mirror-a-share-kpl-list"
    assert parsed.tags == ["炸板"]


def test_ths_member_queries_each_concept_by_ts_code(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")

    class FakeThsMemberClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def ths_index(self, **kwargs):
            assert kwargs == {"src": "THS", "type": "N"}
            return pd.DataFrame(
                {
                    "ts_code": ["883301.TI", "883300.TI", "883300.TI"],
                    "name": ["机器人", "AI", "AI"],
                }
            )

        def ths_member(self, **kwargs):
            self.calls.append(dict(kwargs))
            assert "con_code" not in kwargs
            concept_code = str(kwargs["ts_code"])
            stock_code = {
                "883300.TI": "000001.SZ",
                "883301.TI": "000002.SZ",
            }[concept_code]
            return pd.DataFrame(
                {
                    "ts_code": [concept_code, concept_code],
                    "con_code": [stock_code, stock_code],
                    "con_name": ["样例股票", "样例股票"],
                    "in_date": ["20200101", "20200101"],
                }
            )

    written_frames = []

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        written_frames.append((path, frame.copy()))
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    client = FakeThsMemberClient()
    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)

    manifest = tushare_a_share.mirror_a_share_ths_member(
        tushare_a_share.ThsMemberMirrorOptions(
            out_dir=tmp_path / "ths_member",
            fields=("con_name",),
            request_interval_seconds=0.0,
            request_options={"client": client},
        )
    )

    assert [call["ts_code"] for call in client.calls] == ["883300.TI", "883301.TI"]
    assert all("con_code" not in call for call in client.calls)
    assert all(call["fields"] == "ts_code,con_code,con_name" for call in client.calls)
    assert {path.parent.name for path, _ in written_frames[:-1]} == {
        "ts_code=883300_TI",
        "ts_code=883301_TI",
    }
    assert manifest["schema_version"] == "tushare.ths_member.v1"
    assert manifest["query"]["mode"] == "per_concept"
    assert manifest["query"]["filter_field"] == "ts_code"
    assert manifest["query"]["concept_codes"] == 2
    assert manifest["totals"]["rows"] == 2
    assert manifest["totals"]["symbols"] == 2
    assert written_frames[-1][1]["symbol"].tolist() == ["000001.SZ", "000002.SZ"]


def test_ths_member_keeps_querying_after_an_empty_concept(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")

    class FakeThsMemberClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def ths_index(self, **kwargs):
            assert kwargs == {"src": "THS", "type": "N"}
            return pd.DataFrame({"ts_code": ["883300.TI", "883301.TI"]})

        def ths_member(self, **kwargs):
            self.calls.append(dict(kwargs))
            if kwargs["ts_code"] == "883300.TI":
                return pd.DataFrame()
            return pd.DataFrame(
                {
                    "ts_code": ["883301.TI"],
                    "con_code": ["000002.SZ"],
                    "con_name": ["万科A"],
                }
            )

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    client = FakeThsMemberClient()
    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)

    manifest = tushare_a_share.mirror_a_share_ths_member(
        tushare_a_share.ThsMemberMirrorOptions(
            out_dir=tmp_path / "ths_member",
            request_interval_seconds=0.0,
            request_options={"client": client},
        )
    )

    assert [call["ts_code"] for call in client.calls] == ["883300.TI", "883301.TI"]
    assert all(set(call) == {"ts_code", "fields"} for call in client.calls)
    assert manifest["query"]["mode"] == "per_concept"
    assert manifest["totals"]["rows"] == 1
    assert manifest["totals"]["files"] == 1


def test_ths_member_rejects_a_response_for_another_concept(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")

    class FakeThsMemberClient:
        def ths_index(self, **kwargs):
            assert kwargs == {"src": "THS", "type": "N"}
            return pd.DataFrame({"ts_code": ["883300.TI"]})

        def ths_member(self, **kwargs):
            assert kwargs["ts_code"] == "883300.TI"
            return pd.DataFrame(
                {
                    "ts_code": ["700001.TI"],
                    "con_code": ["000001.SZ"],
                    "con_name": ["平安银行"],
                }
            )

    with pytest.raises(ValueError, match="did not match requested concept ts_code"):
        tushare_a_share.mirror_a_share_ths_member(
            tushare_a_share.ThsMemberMirrorOptions(
                out_dir=tmp_path / "ths_member",
                request_interval_seconds=0.0,
                request_options={"client": FakeThsMemberClient()},
            )
        )


def test_ths_member_rejects_a_response_without_stock_identity(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")

    class FakeThsMemberClient:
        def ths_index(self, **kwargs):
            assert kwargs == {"src": "THS", "type": "N"}
            return pd.DataFrame({"ts_code": ["883300.TI"]})

        def ths_member(self, **kwargs):
            assert kwargs["ts_code"] == "883300.TI"
            return pd.DataFrame({"ts_code": ["883300.TI"], "con_name": ["平安银行"]})

    with pytest.raises(ValueError, match="missing required identity fields: con_code"):
        tushare_a_share.mirror_a_share_ths_member(
            tushare_a_share.ThsMemberMirrorOptions(
                out_dir=tmp_path / "ths_member",
                request_interval_seconds=0.0,
                request_options={"client": FakeThsMemberClient()},
            )
        )


def test_hotspot_event_and_month_mirrors_partition_dates_and_months(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    report_manifest = tushare_a_share.mirror_a_share_report_rc(
        out_dir=tmp_path / "report_rc",
        start_date="20260522",
        end_date="20260523",
        client=client,
    )
    assert [call[1]["start_date"] for call in client.query_calls] == [
        "20260522",
        "20260523",
    ]
    assert report_manifest["dataset"] == "report_rc"
    assert report_manifest["query"]["partition_by"] == "event_date"

    client.query_calls.clear()
    month_manifest = tushare_a_share.mirror_a_share_broker_recommend(
        out_dir=tmp_path / "broker_recommend",
        start_date="20260522",
        end_date="20260616",
        client=client,
    )
    assert [call[1]["month"] for call in client.query_calls] == ["202605", "202606"]
    assert month_manifest["dataset"] == "broker_recommend"
    assert month_manifest["query"]["partition_by"] == "month"

    parser = build_parser()
    required = ["--out-dir", "output", "--start-date", "20260522", "--end-date", "20260525"]
    parsed = parser.parse_args(["tushare", "mirror-a-share-report-rc", *required])
    assert parsed.tushare_command == "mirror-a-share-report-rc"
    parsed = parser.parse_args(["tushare", "mirror-a-share-broker-recommend", *required])
    assert parsed.tushare_command == "mirror-a-share-broker-recommend"


def test_fund_portfolio_mirror_uses_default_fields_and_command_is_exposed(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    manifest = tushare_a_share.mirror_a_share_fund_portfolio(
        out_dir=tmp_path / "a_share_fund_portfolio",
        start_date="20260301",
        end_date="20260630",
        page_size=1,
        client=client,
    )

    assert client.fund_portfolio_periods == [
        "20260331",
        "20260331",
        "20260630",
        "20260630",
    ]
    assert client.fund_portfolio_offsets == [0, 1, 0, 1]
    assert client.fund_portfolio_fields
    assert "mkv" in client.fund_portfolio_fields[0]
    assert "stk_float_ratio" in client.fund_portfolio_fields[0]
    assert manifest["schema_version"] == "tushare.fund_portfolio.v1"
    assert manifest["dataset"] == "fund_portfolio"
    assert manifest["query"]["fields"] == list(tushare_a_share.DEFAULT_FUND_PORTFOLIO_FIELDS)
    assert manifest["totals"]["funds"] == 1
    assert manifest["totals"]["pages"] == 2
    assert manifest["pages_by_period"] == {"20260331": 1, "20260630": 1}

    parser = build_parser()
    required = [
        "--out-dir",
        "output",
        "--start-date",
        "20260301",
        "--end-date",
        "20260630",
        "--page-size",
        "8000",
    ]
    parsed = parser.parse_args(["tushare", "mirror-a-share-fund-portfolio", *required])
    assert parsed.tushare_command == "mirror-a-share-fund-portfolio"
    assert parsed.page_size == 8000


def test_top10_holder_mirrors_use_default_fields_and_symbols(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    manifest = tushare_a_share.mirror_a_share_top10_holders(
        out_dir=tmp_path / "top10_holders",
        symbols=["600519.SH", "000001.SZ"],
        start_date="20240101",
        end_date="20260529",
        client=client,
    )
    assert client.top10_holders_symbols == ["600519.SH", "000001.SZ"]
    assert client.top10_holders_fields
    assert "holder_type" in client.top10_holders_fields[0]
    assert manifest["schema_version"] == "tushare.top10_holders.v1"
    assert manifest["dataset"] == "top10_holders"
    assert manifest["totals"]["rows"] == 4
    assert manifest["totals"]["symbols"] == 2

    float_manifest = tushare_a_share.mirror_a_share_top10_floatholders(
        out_dir=tmp_path / "top10_floatholders",
        symbols=["600519.SH"],
        start_date="20240101",
        end_date="20260529",
        client=client,
    )
    assert client.top10_floatholders_symbols == ["600519.SH"]
    assert "hold_float_ratio" in client.top10_floatholders_fields[0]
    assert float_manifest["schema_version"] == "tushare.top10_floatholders.v1"
    assert float_manifest["dataset"] == "top10_floatholders"

    parser = build_parser()
    required = [
        "--out-dir",
        "output",
        "--start-date",
        "20240101",
        "--end-date",
        "20260529",
        "--symbol",
        "600519.SH",
    ]
    parsed = parser.parse_args(["tushare", "mirror-a-share-top10-holders", *required])
    assert parsed.tushare_command == "mirror-a-share-top10-holders"
    assert parsed.symbols == ["600519.SH"]


def test_stk_holdertrade_mirror_uses_default_fields_and_symbols(monkeypatch, tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)
    manifest = tushare_a_share.mirror_a_share_stk_holdertrade(
        out_dir=tmp_path / "stk_holdertrade",
        symbols=["600519.SH"],
        start_date="20240101",
        end_date="20260529",
        client=client,
    )
    assert client.stk_holdertrade_symbols == ["600519.SH"]
    assert client.stk_holdertrade_fields
    assert "change_vol" in client.stk_holdertrade_fields[0]
    assert "avg_price" in client.stk_holdertrade_fields[0]
    assert manifest["schema_version"] == "tushare.stk_holdertrade.v1"
    assert manifest["dataset"] == "stk_holdertrade"
    assert manifest["query"]["fields"] == list(tushare_a_share.DEFAULT_STK_HOLDERTRADE_FIELDS)

    parser = build_parser()
    required = [
        "--out-dir",
        "output",
        "--start-date",
        "20240101",
        "--end-date",
        "20260529",
        "--symbol",
        "600519.SH",
    ]
    parsed = parser.parse_args(["tushare", "mirror-a-share-stk-holdertrade", *required])
    assert parsed.tushare_command == "mirror-a-share-stk-holdertrade"
    assert parsed.symbols == ["600519.SH"]


def test_backfill_plan_segments_by_month_and_standard_paths(tmp_path):
    plan = build_a_share_backfill_plan(
        artifacts_root=tmp_path,
        start_date="20260130",
        end_date="20260302",
        datasets=["daily", "adj_factor"],
        segment="month",
    )

    assert plan["status"] == "planned"
    assert plan["totals"] == {
        "datasets": 2,
        "segments_per_dataset": 3,
        "dataset_segments": 6,
    }
    assert (
        plan["datasets"][0]["output_dir"]
        .replace("\\", "/")
        .endswith("assets/tushare/a_share/daily/a_share_all_20260130_20260302_daily")
    )
    assert (
        plan["datasets"][0]["latest_alias"]
        .replace("\\", "/")
        .endswith("assets/tushare/a_share/daily/a_share_all_daily_latest")
    )
    assert plan["datasets"][0]["segments"] == [
        {"start_date": "20260130", "end_date": "20260131"},
        {"start_date": "20260201", "end_date": "20260228"},
        {"start_date": "20260301", "end_date": "20260302"},
    ]


def test_backfill_command_is_exposed():
    parser = build_parser()

    parsed = parser.parse_args(
        [
            "tushare",
            "backfill-a-share-history",
            "--artifacts-root",
            "root",
            "--start-date",
            "20260105",
            "--end-date",
            "20260109",
            "--dataset",
            "daily",
            "--segment",
            "year",
            "--dry-run",
        ]
    )

    assert parsed.tushare_command == "backfill-a-share-history"
    assert parsed.datasets == ["daily"]
    assert parsed.segment == "year"
    assert parsed.dry_run is True
    assert parsed.use_proxy is False
    assert parsed.retry_attempts == tushare_a_share.DEFAULT_REQUEST_ATTEMPTS
    assert parsed.retry_sleep_seconds == tushare_a_share.DEFAULT_RETRY_SLEEP_SECONDS
    assert parsed.retry_max_sleep_seconds == tushare_a_share.DEFAULT_RETRY_MAX_SLEEP_SECONDS
    assert parsed.quota_cooldown_seconds == tushare_a_share.DEFAULT_QUOTA_COOLDOWN_SECONDS
    assert parsed.api_url is None

    parsed = parser.parse_args(
        [
            "tushare",
            "backfill-a-share-history",
            "--artifacts-root",
            "root",
            "--start-date",
            "20260105",
            "--end-date",
            "20260109",
            "--use-proxy",
            "--retry-attempts",
            "5",
            "--retry-sleep-seconds",
            "1.5",
            "--retry-max-sleep-seconds",
            "6",
            "--quota-cooldown-seconds",
            "70",
            "--api-url",
            "https://proxy-a.example.com",
        ]
    )

    assert parsed.use_proxy is True
    assert parsed.api_url == "https://proxy-a.example.com"
    assert parsed.retry_attempts == 5
    assert parsed.retry_sleep_seconds == 1.5
    assert parsed.retry_max_sleep_seconds == 6.0
    assert parsed.quota_cooldown_seconds == 70.0


def test_current_refresh_plan_validates_before_publishing_aliases(tmp_path):
    plan = build_a_share_current_refresh_plan(
        artifacts_root=tmp_path,
        start_date="20240102",
        end_date="20260529",
        segment="year",
    )

    assert plan["status"] == "planned"
    assert plan["no_write"] is True
    stages = plan["stages"]
    assert [stage["name"] for stage in stages] == [
        "raw_backfill",
        "daily_clean_build",
        "daily_clean_validate_baseline",
        "daily_clean_validate_research",
        "universe_build",
        "universe_validate",
        "promote_current",
    ]
    assert all(stage["writes_latest_aliases"] is False for stage in stages[:-1])
    assert stages[-1]["writes_latest_aliases"] is True
    assert "--instruments-file" in stages[1]["command"]
    assert stages[2]["command"][stages[2]["command"].index("--profile") + 1] == "baseline"
    assert stages[3]["command"][stages[3]["command"].index("--profile") + 1] == "research"
    assert "--trade-cal-file" in stages[3]["command"]
    assert "/staging/" in stages[4]["command"][stages[4]["command"].index("--out") + 1].replace(
        "\\", "/"
    )
    assert (
        stages[-1]["file_updates"]["universe_by_date"]["destination"]
        .replace("\\", "/")
        .endswith("assets/universe/a_share_all_20240102_20260529_full_by_date.csv")
    )
    assert plan["publication_policy"]["latest_alias_update"] == (
        "after_daily_clean_and_universe_validation"
    )
    assert stages[-1]["command"][-1] == "--apply"


def _write_completed_manifest(  # noqa: PLR0913
    root,
    *,
    dataset,
    start_date="20240102",
    end_date="20260529",
    rows=10,
    symbols=2,
):
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": f"tushare.a_share.{dataset}.v1",
                "dataset": dataset,
                "market": "a_share",
                "provider": "tushare",
                "status": "completed",
                "query": {"start_date": start_date, "end_date": end_date},
                "totals": {"rows": rows, "symbols": symbols, "files": 1},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_manifested_file(
    path,
    *,
    dataset,
    content="demo\n",
    start_date="20240102",
    end_date="20260529",
):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    manifest = path.with_name(f"{path.stem}.manifest.yml")
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema_version": f"tushare.a_share.{dataset}.v1",
                "dataset": dataset,
                "market": "a_share",
                "provider": "tushare",
                "status": "completed",
                "output_dir": str(path),
                "query": {"start_date": start_date, "end_date": end_date},
                "totals": {"rows": 1, "symbols": 1, "files": 1},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _write_passed_report(path, *, dataset):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "dataset": dataset,
                "market": "a_share",
                "provider": "tushare",
                "status": "passed",
                "errors": [],
                "totals": {"rows": 10, "symbols": 2},
            }
        ),
        encoding="utf-8",
    )


def _prepare_current_promotion_inputs(root):
    plan = build_a_share_current_refresh_plan(
        artifacts_root=root,
        start_date="20240102",
        end_date="20260529",
    )
    for row in plan["stages"][0]["plan"]["datasets"]:
        _write_completed_manifest(root.__class__(row["output_dir"]), dataset=row["dataset"])
    daily_clean_dir = root / (
        "assets/tushare/a_share/daily/a_share_all_20240102_20260529_daily_clean"
    )
    _write_completed_manifest(daily_clean_dir, dataset="daily_clean")
    _write_manifested_file(
        root / "assets/tushare/a_share/instruments/a_share_all_instruments_latest.parquet",
        dataset="instruments",
    )
    _write_manifested_file(
        root / "assets/tushare/a_share/trade_cal/a_share_trade_cal_latest.parquet",
        dataset="trade_cal",
    )
    universe_dir = root / "assets" / "universe" / "staging"
    _write_manifested_file(
        universe_dir / "a_share_all_20240102_20260529_full_by_date.csv",
        dataset="universe_by_date",
        content="trade_date,symbol,liq_metric,selected\n20260529,600519.SH,1.0,True\n",
    )
    _write_manifested_file(
        universe_dir / "a_share_all_20240102_20260529_full_symbols.txt",
        dataset="universe_symbols",
        content="600519.SH\n",
    )
    _write_manifested_file(
        universe_dir / "a_share_all_20240102_20260529_full_by_date.meta.yml",
        dataset="universe_meta",
        content="build:\n  last_rebalance_date: '20260529'\n",
    )
    _write_passed_report(
        root / "reports/a_share_daily_clean_baseline_validation_20240102_20260529.json",
        dataset="daily_clean",
    )
    _write_passed_report(
        root / "reports/a_share_daily_clean_research_validation_20240102_20260529.json",
        dataset="daily_clean",
    )
    _write_passed_report(
        root / "reports/a_share_universe_validation_20240102_20260529.json",
        dataset="universe",
    )


def test_current_promotion_dry_run_checks_evidence_without_writing(tmp_path):
    _prepare_current_promotion_inputs(tmp_path)

    summary = run_a_share_current_promotion(
        artifacts_root=tmp_path,
        start_date="20240102",
        end_date="20260529",
    )

    assert summary["status"] == "ready"
    assert summary["dry_run"] is True
    assert "daily_clean_baseline" in summary["checks"]["reports"]
    assert not (tmp_path / "metadata" / "current_assets" / "a_share_current.json").exists()


def test_current_promotion_apply_publishes_contract_registry_and_evidence(tmp_path):
    _prepare_current_promotion_inputs(tmp_path)

    summary = run_a_share_current_promotion(
        artifacts_root=tmp_path,
        start_date="20240102",
        end_date="20260529",
        apply=True,
        fail_on_severity="error",
    )

    assert summary["status"] == "passed"
    daily_alias = tmp_path / "assets/tushare/a_share/daily/a_share_all_daily_clean_latest"
    assert daily_alias.is_dir()
    assert not daily_alias.is_symlink()
    assert (daily_alias / "manifest.yml").is_file()
    current_contract = tmp_path / "metadata/current_assets/a_share_current.json"
    registry = tmp_path / "metadata/dataset_registry.csv"
    evidence = tmp_path / "reports/a_share_current_release_20240102_20260529.json"
    assert current_contract.is_file()
    assert registry.is_file()
    assert evidence.is_file()
    assert (tmp_path / "reports/a_share_current_health_20260529.json").is_file()
    universe_alias = tmp_path / "assets/universe/a_share_all_full_by_date.csv"
    universe_version = tmp_path / "assets/universe/a_share_all_20240102_20260529_full_by_date.csv"
    assert universe_alias.is_file()
    assert not universe_alias.is_symlink()
    assert universe_version.is_file()
    assert (
        tmp_path / "assets/universe/a_share_all_20240102_20260529_full_by_date.manifest.yml"
    ).is_file()
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["publication"]["current_contract"] == str(current_contract)
    assert payload["quality_verdict"]["gate_status"] == "pass"


def test_current_promotion_blocks_failed_validation_report(tmp_path):
    _prepare_current_promotion_inputs(tmp_path)
    report = tmp_path / "reports/a_share_universe_validation_20240102_20260529.json"
    report.write_text(json.dumps({"status": "failed", "errors": ["bad"]}), encoding="utf-8")

    with pytest.raises(ValueError, match="universe validation report status must be passed"):
        run_a_share_current_promotion(
            artifacts_root=tmp_path,
            start_date="20240102",
            end_date="20260529",
            apply=True,
        )


def test_current_refresh_plan_requires_all_daily_clean_inputs(tmp_path):
    with pytest.raises(ValueError, match="requires all daily_clean raw inputs"):
        build_a_share_current_refresh_plan(
            artifacts_root=tmp_path,
            start_date="20240102",
            end_date="20260529",
            datasets=["daily"],
        )


def test_current_refresh_plan_command_is_exposed():
    parser = build_parser()

    parsed = parser.parse_args(
        [
            "tushare",
            "plan-a-share-current-refresh",
            "--artifacts-root",
            "root",
            "--start-date",
            "20240102",
            "--end-date",
            "20260529",
        ]
    )

    assert parsed.tushare_command == "plan-a-share-current-refresh"
    assert parsed.start_date == "20240102"
    assert parsed.end_date == "20260529"


def test_current_promotion_command_is_exposed():
    parser = build_parser()

    parsed = parser.parse_args(
        [
            "tushare",
            "promote-a-share-current",
            "--artifacts-root",
            "root",
            "--start-date",
            "20240102",
            "--end-date",
            "20260529",
            "--required-asset",
            "daily_clean",
            "--apply",
        ]
    )

    assert parsed.tushare_command == "promote-a-share-current"
    assert parsed.required_assets == ["daily_clean"]
    assert parsed.apply is True


def test_replace_latest_symlink_replaces_alias_itself(tmp_path):
    old_target = tmp_path / "old"
    new_target = tmp_path / "new"
    old_target.mkdir()
    new_target.mkdir()
    (new_target / "payload.txt").write_text("new", encoding="utf-8")
    alias = tmp_path / "latest"
    alias.symlink_to("old")

    result = _replace_latest_symlink(alias_path=alias, target=new_target)

    assert alias.is_dir()
    assert not alias.is_symlink()
    assert (alias / "payload.txt").read_text(encoding="utf-8") == "new"
    assert result["alias_path"] == str(alias)
    assert result["target"] == str(new_target)


def test_daily_clean_validation_cli_exposes_streaming_quality_options():
    parser = build_parser()

    parsed = parser.parse_args(
        [
            "tushare",
            "validate-a-share-daily-clean",
            "--daily-clean-dir",
            "daily-clean",
            "--profile",
            "research",
            "--trade-cal-file",
            "trade-cal.parquet",
            "--fail-on-severity",
            "warning",
            "--max-warning-rate",
            "0.01",
            "--pct-chg-tolerance",
            "0.1",
            "--batch-rows",
            "4096",
            "--memory-soft-limit-mb",
            "1024",
            "--memory-hard-limit-mb",
            "512",
            "--out",
            "report.json",
        ]
    )

    assert parsed.profile == "research"
    assert parsed.trade_cal_file == "trade-cal.parquet"
    assert parsed.fail_on_severity == "warning"
    assert parsed.max_warning_rate == 0.01
    assert parsed.pct_chg_tolerance == 0.1
    assert parsed.batch_rows == 4096
    assert parsed.memory_soft_limit_mb == 1024
    assert parsed.memory_hard_limit_mb == 512
    assert parsed.out == "report.json"


def test_backfill_writes_range_manifest_and_skips_existing_partitions(tmp_path):
    pd = pytest.importorskip("pandas")
    client = FakeDataClient(pd)

    summary = run_a_share_history_backfill(
        AShareHistoryBackfillOptions(
            artifacts_root=tmp_path,
            start_date="20260522",
            end_date="20260525",
            datasets=["daily"],
            segment="month",
            client=client,
        )
    )

    assert summary["status"] == "completed"
    assert client.daily_dates == ["20260522", "20260525"]
    dataset = summary["datasets"][0]
    assert dataset["totals"]["rows"] == 2
    assert dataset["totals"]["symbols"] == 1
    assert dataset["totals"]["trade_dates_present"] == 2
    assert dataset["request_policy"]["disable_proxy"] is True

    client.daily_dates.clear()
    resumed = run_a_share_history_backfill(
        artifacts_root=tmp_path,
        start_date="20260522",
        end_date="20260525",
        datasets=["daily"],
        segment="month",
        client=client,
    )

    assert resumed["status"] == "completed"
    assert client.daily_dates == []
    resumed_totals = resumed["datasets"][0]["totals"]
    assert resumed_totals["rows"] == 2
    assert resumed_totals["trade_dates_written_this_run"] == 0
    assert resumed_totals["trade_dates_skipped_this_run"] == 2

    manifest_path = tmp_path / (
        "assets/tushare/a_share/daily/a_share_all_20260522_20260525_daily/manifest.yml"
    )
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert manifest["query"]["start_date"] == "20260522"
    assert manifest["query"]["end_date"] == "20260525"
    assert manifest["totals"]["files"] == 2
    assert manifest["request_policy"]["attempts"] == tushare_a_share.DEFAULT_REQUEST_ATTEMPTS
    assert manifest["request_policy"]["disable_proxy"] is True


def test_backfill_sync_latest_points_alias_at_completed_snapshot(tmp_path):
    pd = pytest.importorskip("pandas")

    summary = run_a_share_history_backfill(
        artifacts_root=tmp_path,
        start_date="20260522",
        end_date="20260525",
        datasets=["daily"],
        segment="all",
        sync_latest=True,
        client=FakeDataClient(pd),
    )

    alias = tmp_path / "assets/tushare/a_share/daily/a_share_all_daily_latest"
    assert summary["datasets"][0]["latest_alias"]["alias_path"] == str(alias)
    assert summary["datasets"][0]["latest_alias"]["target"].endswith(
        "a_share_all_20260522_20260525_daily"
    )
    assert alias.is_dir()
    assert not alias.is_symlink()
    assert (alias / "manifest.yml").is_file()


def test_mirror_a_share_index_daily_writes_single_asset(tmp_path, monkeypatch):
    pd = pytest.importorskip("pandas")

    class FakeIndexDailyClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def index_daily(self, **kwargs):
            self.calls.append(dict(kwargs))
            ts_code = str(kwargs["ts_code"])
            return pd.DataFrame(
                {
                    "ts_code": [ts_code, ts_code],
                    "trade_date": ["20260105", "20260106"],
                    "close": [4758.9, 4737.6],
                    "pct_chg": [0.45, -0.82],
                }
            )

    client = FakeIndexDailyClient()
    manifest = tushare_a_share.mirror_a_share_index_daily(
        tushare_a_share.IndexDailyMirrorOptions(
            index_codes=("000300.SH", "000905.SH"),
            out_dir=tmp_path / "index_daily",
            start_date="20260101",
            end_date="20260110",
            request_interval_seconds=0.0,
            request_options={"client": client},
        )
    )

    assert [call["ts_code"] for call in client.calls] == ["000300.SH", "000905.SH"]
    assert all(call["start_date"] == "20260101" for call in client.calls)
    assert manifest["dataset"] == "index_daily"
    assert manifest["totals"]["rows"] == 4
    assert manifest["totals"]["index_codes_written"] == 2
    assert manifest["totals"]["index_codes_empty"] == 0
    written = tmp_path / "index_daily" / "data" / "part.parquet"
    assert written.exists()
    frame = pd.read_parquet(written)
    assert "symbol" in frame.columns
    assert len(frame) == 4


def test_mirror_a_share_index_daily_skips_empty_and_requires_single_output(tmp_path, monkeypatch):
    pd = pytest.importorskip("pandas")

    class FakeIndexDailyClient:
        def index_daily(self, **kwargs):
            if str(kwargs["ts_code"]) == "000300.SH":
                return pd.DataFrame(
                    {
                        "ts_code": ["000300.SH"],
                        "trade_date": ["20260106"],
                        "close": [4758.9],
                        "pct_chg": [0.45],
                    }
                )
            return pd.DataFrame()

    def write_stub(frame, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(frame.to_csv(index=False), encoding="utf-8")

    monkeypatch.setattr(tushare_a_share, "_write_frame", write_stub)

    manifest = tushare_a_share.mirror_a_share_index_daily(
        tushare_a_share.IndexDailyMirrorOptions(
            index_codes=("000300.SH", "000905.SH"),
            out_dir=tmp_path / "index_daily",
            start_date="20260101",
            end_date="20260110",
            request_interval_seconds=0.0,
            request_options={"client": FakeIndexDailyClient()},
        )
    )

    assert manifest["totals"]["rows"] == 1
    assert manifest["totals"]["index_codes_written"] == 1
    assert manifest["totals"]["index_codes_empty"] == 1
    assert manifest["written_index_codes"] == ["000300.SH"]
    assert manifest["empty_index_codes"] == ["000905.SH"]


def test_mirror_a_share_index_daily_requires_non_empty_codes(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="index_codes"):
        tushare_a_share.mirror_a_share_index_daily(
            tushare_a_share.IndexDailyMirrorOptions(
                index_codes=(),
                out_dir=tmp_path / "index_daily",
                start_date="20260101",
                end_date="20260110",
            )
        )


def test_token2_uses_explicit_configured_endpoint_order(monkeypatch) -> None:
    monkeypatch.setenv(
        "TUSHARE_API_URLS_2",
        "https://proxy-a.example.com,https://proxy-b.example.com",
    )
    monkeypatch.delenv("TUSHARE_API_URL_2", raising=False)

    assert resolve_tushare_api_urls(token_env="TUSHARE_TOKEN_2") == (
        "https://proxy-a.example.com",
        "https://proxy-b.example.com",
    )


def test_token2_without_configured_endpoint_has_no_private_fallback(monkeypatch) -> None:
    monkeypatch.delenv("TUSHARE_API_URLS_2", raising=False)
    monkeypatch.delenv("TUSHARE_API_URL_2", raising=False)
    monkeypatch.delenv("TUSHARE_API_URL", raising=False)

    assert resolve_tushare_api_urls(token_env="TUSHARE_TOKEN_2") == ()


def test_endpoint_failover_rotates_only_transport_failures() -> None:
    class Client:
        def __init__(self) -> None:
            self._DataApi__http_url = ""
            self.urls: list[str] = []

        def daily(self) -> str:
            self.urls.append(self._DataApi__http_url)
            if self._DataApi__http_url.endswith("proxy-a.example.com"):
                raise TimeoutError("endpoint timed out")
            return "ok"

    client = Client()
    failover = _TushareEndpointFailover(
        client, ("https://proxy-a.example.com", "https://proxy-b.example.com")
    )

    assert failover.daily() == "ok"
    assert client.urls == ["https://proxy-a.example.com", "https://proxy-b.example.com"]
