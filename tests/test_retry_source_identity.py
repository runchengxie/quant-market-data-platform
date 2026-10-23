import shutil
from pathlib import Path

import pytest

from quant_market_data_platform.providers import tushare_fundamentals_retry as retry


def release(tmp_path):
    sha = "a" * 40
    source = tmp_path / "releases" / sha
    package = source / "src/quant_market_data_platform/providers"
    shutil.copytree(
        Path(retry.__file__).resolve().parents[1],
        package.parent,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    script = source / "scripts/operations/archive_tushare_fundamentals_vintage.py"
    script.parent.mkdir(parents=True)
    script.write_bytes((Path(__file__).parents[1] / script.relative_to(source)).read_bytes())
    (source / "pyproject.toml").write_text("[project]\n")
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    manifest = manifests / f"{sha}.txt"
    manifest.write_text(
        f"framework=quant-market-data-platform\ncommit={sha}\nrelease_format=git-archive-v1\n"
    )
    return source, manifest


def test_archive_identity_without_git(tmp_path):
    source, _ = release(tmp_path)
    assert retry.source_identity(source) == (source, "a" * 40, False)


@pytest.mark.parametrize(
    "damage", ["missing", "duplicate", "commit", "format", "framework", "malformed"]
)
def test_archive_identity_rejects_invalid_provenance(tmp_path, damage):
    source, manifest = release(tmp_path)
    content = manifest.read_text()
    if damage == "missing":
        manifest.unlink()
    else:
        content = {
            "duplicate": content + "commit=" + "a" * 40 + "\n",
            "commit": content.replace("commit=a", "commit=b"),
            "format": content.replace("git-archive-v1", "unknown"),
            "framework": content.replace("framework=quant-market-data-platform", "framework=other"),
            "malformed": content + "broken\n",
        }[damage]
        manifest.write_text(content)
    with pytest.raises(RuntimeError, match="source identity.*unproven"):
        retry.source_identity(source)


def test_noneditable_archive_install_resolves_source_and_rejects_legacy(tmp_path):
    import json
    import os
    import subprocess
    import sys
    import sysconfig
    import venv

    source, _ = release(tmp_path)
    (source / "pyproject.toml").write_text(
        '[build-system]\nrequires=["setuptools"]\nbuild-backend="setuptools.build_meta"\n'
        '[project]\nname="quant-market-data-platform"\nversion="0.2.0"\n'
        '[tool.setuptools.packages.find]\nwhere=["src"]\n'
    )
    environment = tmp_path / "venv"
    venv.EnvBuilder(system_site_packages=True, symlinks=True).create(environment)
    python = environment / "bin/python"
    site_packages = (
        environment / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    site_packages.mkdir(parents=True, exist_ok=True)
    (site_packages / "test_dependencies.pth").write_text(sysconfig.get_path("purelib"))
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), "--no-deps", str(source)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    legacy = tmp_path / "legacy20261022"
    legacy.mkdir()
    (legacy / "archive_state.json").write_bytes(b'{"legacy":true}\n')
    code = """
import argparse, json, pathlib, sys
from quant_market_data_platform.providers import tushare_fundamentals_retry as retry
assert pathlib.Path(retry.__file__).is_relative_to(pathlib.Path(sys.prefix))
args = argparse.Namespace(snapshot_date='20261022', start_date='20200101', end_date='20261022',
    datasets=['income'], api_url='https://example.com', field_mappings=['roe=roe'],
    available_delay_days=1, max_observation_age_days=7, observation_frequency='daily',
    token_env='UNUSED', retry_attempts=1, retry_backoff_seconds=0, request_interval_seconds=0,
    page_size=10, max_pages=1, bucket_count=1, batch_rows=10)
parameters = retry.resolved_retry_parameters(args)
assert parameters['source_revision'] == 'a' * 40
assert parameters['source_dirty'] is False
import importlib.util
source = retry.source_identity()[0]
script = source / 'scripts/operations/archive_tushare_fundamentals_vintage.py'
spec = importlib.util.spec_from_file_location('archive', script)
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)
assert archive.resolved_retry_parameters(args) == parameters
assert parameters['semantic']['runtime']['packages']['pandas']
root = pathlib.Path(sys.argv[1])
before = {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
try:
    retry.check_retry_identity(root, parameters)
except RuntimeError as error:
    assert 'legacy' in str(error)
else:
    raise AssertionError('legacy accepted')
assert before == {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
print(json.dumps(parameters))
"""
    result = subprocess.run(
        [str(python), "-c", code, str(legacy)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert json.loads(result.stdout)["source_revision"] == "a" * 40
    # Both recorded install provenance and explicit script provenance reject changed code.
    module = source / "src/quant_market_data_platform/providers/tushare_fundamentals_retry.py"
    module.write_text(module.read_text() + "\n# changed after install\n")
    for expression in ("None", repr(str(source))):
        result = subprocess.run(
            [
                str(python),
                "-c",
                "from pathlib import Path; "
                "from quant_market_data_platform.providers.tushare_retry_source "
                "import source_identity; "
                f"source_identity(Path({expression}) if {expression} else None)",
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
        )
        assert result.returncode != 0
        assert "source identity is unproven" in result.stderr


@pytest.mark.parametrize(
    "record",
    [
        None,
        "{",
        "[]",
        "{}",
        '{"url":"https://example.com/source"}',
        '{"url":"file://other/source"}',
        '{"url":"file:///source?unknown=1"}',
    ],
)
def test_unknown_installed_source_provenance_is_rejected(monkeypatch, record):
    from types import SimpleNamespace

    from quant_market_data_platform.providers import tushare_retry_source as provenance

    monkeypatch.setattr(
        provenance.metadata,
        "distribution",
        lambda name: SimpleNamespace(read_text=lambda filename: record),
    )
    with pytest.raises((ValueError, TypeError)):
        provenance._installed_source()


def test_archive_release_path_must_match_recorded_commit(tmp_path):
    source, _ = release(tmp_path)
    moved = source.with_name("b" * 40)
    source.rename(moved)
    with pytest.raises(RuntimeError, match="source identity.*unproven"):
        retry.source_identity(moved)
