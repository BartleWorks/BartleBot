"""Tests for tools/sign_feed.py: making a key, signing the feed, and checking it against TRUSTED_KEYS."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib

import pytest

from bartlebot.paths import project_root
from bartlebot.updates.signature import verify

sys.path.insert(0, str(project_root()))

import tools.sign_feed as sign_feed  # noqa: E402
from tools.sign_feed import KEY_ENV_VAR, main, private_key, public_text  # noqa: E402

FEED = b'{"Assets":[]}'


@pytest.fixture
def made_key(tmp_path, capsys):
    """A key ``generate`` made: its file, and the public key it printed."""
    path = tmp_path / "key.txt"
    assert main(["generate", str(path)]) == 0
    return path, capsys.readouterr().out.strip()


def test_generate_writes_the_private_key_and_prints_its_public_half(made_key):
    path, public = made_key
    assert public_text(private_key(path.read_text())) == public


def test_generate_never_overwrites_a_key(made_key):
    path, _ = made_key
    before = path.read_text()
    assert main(["generate", str(path)]) == 1
    assert path.read_text() == before


def test_sign_then_verify_against_the_keys_the_app_trusts(made_key, tmp_path, monkeypatch):
    path, public = made_key
    feed = tmp_path / "releases.win.json"
    feed.write_bytes(FEED)
    monkeypatch.setenv(KEY_ENV_VAR, path.read_text())
    monkeypatch.setattr(sign_feed, "TRUSTED_KEYS", (public,))

    assert main(["sign", str(feed)]) == 0
    signature = tmp_path / "releases.win.json.sig"
    assert verify(FEED, signature.read_bytes(), (public,))
    assert main(["verify", str(feed)]) == 0


def test_verify_fails_a_secret_the_app_does_not_trust(made_key, tmp_path, monkeypatch, capsys):
    """So a wrong secret fails the release, rather than shipping a feed no install accepts."""
    path, _ = made_key
    feed = tmp_path / "releases.win.json"
    feed.write_bytes(FEED)
    monkeypatch.setenv(KEY_ENV_VAR, path.read_text())

    assert main(["sign", str(feed)]) == 0
    assert main(["verify", str(feed)]) == 1
    assert "TRUSTED_KEYS" in capsys.readouterr().err


def test_sign_without_the_secret_fails(tmp_path, monkeypatch):
    feed = tmp_path / "releases.win.json"
    feed.write_bytes(FEED)
    monkeypatch.delenv(KEY_ENV_VAR, raising=False)
    assert main(["sign", str(feed)]) == 1
    assert not (tmp_path / "releases.win.json.sig").exists()


def test_sign_with_a_secret_that_is_not_a_key_fails(tmp_path, monkeypatch):
    feed = tmp_path / "releases.win.json"
    feed.write_bytes(FEED)
    monkeypatch.setenv(KEY_ENV_VAR, "c2hvcnQ=")
    assert main(["sign", str(feed)]) == 1


def test_verify_without_a_signature_fails(tmp_path):
    feed = tmp_path / "releases.win.json"
    feed.write_bytes(FEED)
    assert main(["verify", str(feed)]) == 1


# -- release.yml's signing job ---------------------------------------------------------

RELEASE_YML = project_root() / ".github" / "workflows" / "release.yml"


def _jobs() -> dict[str, str]:
    """``release.yml``'s jobs by name, each its own text."""
    body = RELEASE_YML.read_text(encoding="utf-8").split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([a-z_-]+):\n", body, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2]))


def test_only_the_signing_job_can_read_the_key():
    jobs = _jobs()

    assert set(jobs) == {"build", "sign", "publish"}
    assert [name for name, text in jobs.items() if "secrets.FEED_SIGNING_KEY" in text] == ["sign"]
    assert "write" not in jobs["sign"], "the job holding the key never gets a token that can write"


def test_the_signing_job_runs_in_the_release_environment():
    jobs = _jobs()

    assert "environment: release" in jobs["sign"], "the key is a secret of the release environment"
    assert [name for name, text in jobs.items() if "environment:" in text] == ["sign"]


def test_the_signing_job_signs_then_checks_against_the_apps_keys():
    sign_job = _jobs()["sign"]

    assert "needs: build" in sign_job
    assert sign_job.index("sign_feed.py sign") < sign_job.index("sign_feed.py verify")


def test_publishing_checks_the_feed_it_uploads_and_attaches_the_signature_after_the_draft():
    publish = _jobs()["publish"]

    assert "needs: [build, sign]" in publish
    assert publish.index("sign_feed.py verify") < publish.index("upload github")
    assert publish.index("upload github") < publish.index("gh release upload")
    assert "releases.win.json.sig" in publish[publish.index("gh release upload") :]


def test_the_signed_feed_replaces_the_one_vpk_uploads():
    """vpk uploads a feed listing only this release's packages, which the signature does not cover."""
    publish = _jobs()["publish"]
    upload = publish[publish.index("gh release upload") :].split("\n\n", 1)[0]

    assert "releases/releases.win.json " in upload and "releases/releases.win.json.sig" in upload
    assert "--clobber" in upload


def test_the_last_step_checks_the_feed_on_the_draft_itself():
    """What an installed app reads is what is on the release, so that is what is verified last."""
    publish = _jobs()["publish"]
    last = publish.rstrip().rsplit("- name:", 1)[1]

    assert "gh release download" in last and "releases.win.json.sig" in last
    assert last.index("gh release download") < last.index("sign_feed.py verify drafted/releases.win.json")
    assert publish.index("gh release upload") < publish.index("gh release download")


#: What the jobs that run sign_feed.py install: cryptography alone (``--no-project``). So the
#: script may import the standard library, cryptography, and these modules of the app.
_SIGNING_JOB_MODULES = {"bartlebot", "bartlebot.updates", "bartlebot.updates.keys", "bartlebot.updates.signature"}

_REFUSE_OTHER_IMPORTS = """
import runpy, sys
allowed = set(sys.argv[1].split(","))
class Refuse:
    def find_spec(self, name, path=None, target=None):
        top = name.partition(".")[0]
        if top in sys.stdlib_module_names or top in ("cryptography", "_cffi_backend") or name in allowed:
            return None
        raise ModuleNotFoundError(f"No module named {name!r} in the signing job", name=name)
sys.meta_path.insert(0, Refuse())
sys.argv = [sys.argv[2], *sys.argv[3:]]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


def test_sign_feed_imports_only_cryptography_and_the_signature(tmp_path):
    """The signing job installs cryptography alone, so anything more fails the release.

    0.3.0's first tag failed this way: sign_feed.py imported verify from feed.py, which
    imports bartleby-core.
    """
    script = project_root() / "tools" / "sign_feed.py"
    key_file = tmp_path / "key.txt"
    run = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            _REFUSE_OTHER_IMPORTS,
            ",".join(_SIGNING_JOB_MODULES),
            str(script),
            "generate",
            str(key_file),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert run.returncode == 0, run.stderr
    assert key_file.is_file()


def test_the_signing_job_installs_the_locked_cryptography():
    lock = tomllib.loads((project_root() / "uv.lock").read_text(encoding="utf-8"))
    (locked,) = [p["version"] for p in lock["package"] if p["name"] == "cryptography"]

    assert f'CRYPTOGRAPHY_VERSION: "{locked}"' in RELEASE_YML.read_text(encoding="utf-8")
