"""The connection recipes in examples/ must keep working."""
import importlib.util
import os
import py_compile
import shutil
import subprocess
from unittest import mock

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
EX = os.path.join(ROOT, "examples")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(EX, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ROWS = [
    {"symbol": "NSE:ABC", "description": "ABC Ltd", "close": 1234.5, "change": 5.25, "volume": 25_000_000},
    {"symbol": "NSE:XYZ", "name": "XYZ", "close": 99.0, "change": -1.5, "volume": 450_000},
]


def test_telegram_message_is_short_plain_and_honest_about_delay():
    mod = load("telegram_movers")
    text = mod.build_message(ROWS, "stocks-india", "gainers", footer="")
    assert text.startswith("Top gainers: India (NSE)\nPrices are about 15 minutes delayed.\n\n")
    assert "1. ABC Ltd (NSE:ABC)\n   ₹1,234.50   +5.25%   Volume 2.50 Cr" in text
    assert "2. XYZ (NSE:XYZ)\n   ₹99.00   -1.50%   Volume 4.50 L" in text
    assert "http" not in text and text.endswith("\n") and not text.endswith("\n\n")


def test_telegram_crypto_has_no_delay_line_and_footer_is_optional():
    mod = load("telegram_movers")
    text = mod.build_message([{"symbol": "BINANCE:BTCUSDT", "name": "BTC", "close": 85000, "change": 1.2, "volume": 2_500_000}],
                             "crypto", "losers", footer="Sent by my bot")
    assert "delayed" not in text and text.rstrip().endswith("Sent by my bot") and "Volume 2.50M" in text


def test_telegram_dry_run_prints_and_sends_nothing(capsys):
    mod = load("telegram_movers")
    with mock.patch.object(mod, "TickvaleClient") as client, mock.patch.object(mod.requests, "post") as post:
        client.return_value.movers.return_value = ROWS
        assert mod.main(["--dry-run", "--limit", "2"]) == 0
    assert "ABC Ltd" in capsys.readouterr().out
    post.assert_not_called()


def test_telegram_send_needs_credentials_and_posts_to_telegram(monkeypatch):
    mod = load("telegram_movers")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with mock.patch.object(mod, "TickvaleClient") as client:
        client.return_value.movers.return_value = ROWS
        assert mod.main([]) == 2
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "@chan")
        with mock.patch.object(mod.requests, "post") as post:
            assert mod.main([]) == 0
    assert post.call_args.args[0] == "https://api.telegram.org/bot123:abc/sendMessage"
    assert post.call_args.kwargs["json"]["chat_id"] == "@chan"


@pytest.mark.parametrize("name", ["python_quickstart", "telegram_movers"])
def test_python_examples_compile(name):
    py_compile.compile(os.path.join(EX, f"{name}.py"), doraise=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("name", ["node_quickstart.mjs", "google_sheets.gs"])
def test_javascript_examples_have_valid_syntax(name, tmp_path):
    src = os.path.join(EX, name)
    target = tmp_path / (name if name.endswith(".mjs") else name + ".js")
    shutil.copy(src, target)
    result = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_curl_example_is_valid_shell():
    assert subprocess.run(["bash", "-n", os.path.join(EX, "curl.sh")], capture_output=True).returncode == 0


def test_example_tools_mentioned_in_the_docs_exist():
    for path in ("curl.sh", "python_quickstart.py", "node_quickstart.mjs", "google_sheets.gs", "telegram_movers.py"):
        assert os.path.exists(os.path.join(EX, path)), path
