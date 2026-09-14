from __future__ import annotations

from market_data_platform.cli import build_parser


def test_context_cli_registers_fetch_build_publish_and_inspect():
    parser = build_parser()
    for argv, command in (
        (["context", "fetch", "--provider", "tushare", "--dataset", "shibor"], "fetch"),
        (
            [
                "context",
                "fetch",
                "--provider",
                "nbs",
                "--dataset",
                "industrial_value_added_yoy",
                "--period",
                "202607",
            ],
            "fetch",
        ),
        (
            [
                "context",
                "fetch",
                "--provider",
                "nea",
                "--dataset",
                "electricity",
                "--source-url",
                "https://www.nea.gov.cn/example/c.html",
            ],
            "fetch",
        ),
        (["context", "build", "--as-of", "20260828"], "build"),
        (["context", "publish", "--as-of", "20260828"], "publish"),
        (["context", "inspect", "--as-of", "20260828"], "inspect"),
    ):
        args = parser.parse_args(argv)
        assert args.command == "context"
        assert args.context_command == command
        assert callable(args.handler)


def test_context_fetch_rejects_unknown_provider():
    parser = build_parser()
    try:
        parser.parse_args(["context", "fetch", "--provider", "unknown", "--dataset", "shibor"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("unknown context provider must be rejected")
