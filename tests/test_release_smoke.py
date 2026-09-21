"""C49 release metadata and credential-free demonstration smoke tests."""

from apps.api.demo import run_demo
from apps.api.main import app


def test_release_version_and_fixture_demo() -> None:
    summary = run_demo()
    assert app.version == "1.0.0"
    assert summary.release == "1.0.0"
    assert summary.state == "succeeded"
    assert summary.destination == "北京"
    assert summary.plan_count == 3
    assert summary.artifact_count == 6
    assert summary.event_count == 7
    assert "合成 Fixture" in summary.fixture_warning
