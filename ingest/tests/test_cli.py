from engrebodds_ingest.__main__ import main
from engrebodds_ingest.config import load_settings


def test_list_jobs(capsys):
    assert main(["--list"]) == 0
    assert "Available jobs" in capsys.readouterr().out


def test_unknown_job():
    assert main(["no_such_job"]) == 2


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("API_QUOTA_FLOOR", raising=False)
    assert load_settings().quota_floor == 500
