from pathlib import Path
import subprocess

import pytest

from mouse_control import updater


def result(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


def release(version="0.8.0", assets=()):
    return updater.Release(version, tuple(assets))


def asset(name):
    return {
        "name": name,
        "browser_download_url": f"https://github.com/DonGeronimo7/mouse-control/releases/download/v0.8.0/{name}",
    }


@pytest.mark.parametrize("candidate", ["../../something", "../mouse-control.rpm", "/tmp/evil", "foo/../../evil", "\\\\server\\evil", "", ".", ".."]) 
def test_unsafe_asset_filenames_are_rejected(candidate):
    with pytest.raises(updater.UpdateError, match="unsafe filename"):
        updater._safe_asset_filename(candidate)


def test_asset_selection_requires_project_version_and_compatible_architecture(monkeypatch):
    monkeypatch.setattr(updater, "_architecture", lambda: "x86_64")
    assert updater.select_asset(release(assets=(asset("mouse-control-0.8.0-1.fc44.noarch.rpm"),)), ".rpm")["name"].endswith("noarch.rpm")
    assert updater.select_asset(release(assets=(asset("mouse-control_0.8.0_all.deb"),)), ".deb")["name"].endswith("_all.deb")
    assert updater.select_asset(release(assets=(asset("Mouse-Control-0.8.0-x86_64.AppImage"),)), ".appimage")["name"].endswith("AppImage")
    for suffix, name in [
        (".rpm", "unrelated-0.8.0-1.x86_64.rpm"),
        (".rpm", "mouse-control-0.8.0-1.aarch64.rpm"),
        (".deb", "unrelated_0.8.0_all.deb"),
        (".deb", "mouse-control_0.8.0_arm64.deb"),
        (".appimage", "Mouse-Control-0.8.0-aarch64.AppImage"),
    ]:
        with pytest.raises(updater.UpdateError, match="identify one"):
            updater.select_asset(release(assets=(asset(name),)), suffix)


def test_asset_selection_normalizes_architecture_and_rejects_ambiguity(monkeypatch):
    monkeypatch.setattr(updater, "_architecture", lambda: "x86_64")
    assert updater.select_asset(release(assets=(asset("mouse-control-0.8.0-1.amd64.rpm"),)), ".rpm")["name"].endswith("amd64.rpm")
    with pytest.raises(updater.UpdateError, match="identify one"):
        updater.select_asset(release(assets=(
            asset("mouse-control-0.8.0-1.noarch.rpm"),
            asset("mouse-control-0.8.0-2.noarch.rpm"),
        )), ".rpm")


@pytest.mark.parametrize(("installed", "latest", "expected"), [
    ("0.7.4", "0.8.0", True), ("0.9.9", "0.10.0", True),
    ("v0.7.4", "v0.7.4", False), ("0.7.4", "0.8rc1", True),
])
def test_version_comparison_uses_pep440(installed, latest, expected):
    assert updater.is_newer(latest, installed) is expected


def test_malformed_version_is_rejected_cleanly():
    with pytest.raises(updater.UpdateError, match="Unsupported release version"):
        updater.is_newer("not a version", "0.7.4")


def _source_module(tmp_path):
    checkout = tmp_path / "Mouse-control"
    (checkout / ".git").mkdir(parents=True)
    module = checkout / "src" / "mouse_control" / "updater.py"
    module.parent.mkdir(parents=True)
    module.touch()
    return checkout, module


def test_source_tree_wins_over_another_rpm_install_and_metadata(monkeypatch, tmp_path):
    checkout, module = _source_module(tmp_path)
    executable = checkout / "src" / "mouse_control" / "cli.py"
    monkeypatch.setattr(updater, "__file__", str(module))
    monkeypatch.setattr(updater, "_owned_by", lambda command: "mouse-control-0.7.4" if command[-1] == "/usr/bin/mouse-control" else None)
    monkeypatch.setattr(updater.importlib.metadata, "distribution", lambda _: object())
    install = updater.detect_installation(executable)
    assert install.kind == "source"
    assert install.detail == f"running from {checkout}"


def test_real_pip_install_remains_pip_when_loaded_code_is_not_source(monkeypatch, tmp_path):
    module = tmp_path / "site-packages" / "mouse_control" / "updater.py"
    module.parent.mkdir(parents=True)
    module.touch()
    class Dist:
        def read_text(self, name): return None
    monkeypatch.setattr(updater, "__file__", str(module))
    monkeypatch.setattr(updater, "_owned_by", lambda _: None)
    monkeypatch.setattr(updater.importlib.metadata, "distribution", lambda _: Dist())
    assert updater.detect_installation(Path("/home/me/.local/bin/mouse-control")).kind == "pip"


def test_editable_git_install_is_source_and_never_upgraded(monkeypatch, tmp_path):
    module = tmp_path / "site-packages" / "mouse_control" / "updater.py"
    module.parent.mkdir(parents=True)
    module.touch()
    class Dist:
        def read_text(self, name): return '{"dir_info": {"editable": true}}'
    monkeypatch.setattr(updater, "__file__", str(module))
    monkeypatch.setattr(updater, "_owned_by", lambda _: None)
    monkeypatch.setattr(updater.importlib.metadata, "distribution", lambda _: Dist())
    assert updater.detect_installation(Path("/home/me/.local/bin/mouse-control")).kind == "source"


@pytest.mark.parametrize(("owner", "kind"), [("mouse-control", "rpm"), ("mouse-control: /usr/bin/mouse-control", "deb")])
def test_system_package_ownership_remains_authoritative(monkeypatch, tmp_path, owner, kind):
    module = tmp_path / "site-packages" / "mouse_control" / "updater.py"
    module.parent.mkdir(parents=True)
    module.touch()
    monkeypatch.setattr(updater, "__file__", str(module))
    monkeypatch.setattr(updater, "_owned_by", lambda command: owner if command[0] == ("rpm" if kind == "rpm" else "dpkg-query") else None)
    installation = updater.detect_installation(Path("/usr/bin/mouse-control"))
    assert installation.kind == kind
    assert installation.package == "mouse-control"


def test_rpm_ownership_queries_stable_package_name(monkeypatch, tmp_path):
    module = tmp_path / "site-packages" / "mouse_control" / "updater.py"
    module.parent.mkdir(parents=True)
    module.touch()
    monkeypatch.setattr(updater, "__file__", str(module))
    monkeypatch.setattr(updater.shutil, "which", lambda command: "/usr/bin/rpm" if command == "rpm" else None)
    commands = []

    def runner(args):
        commands.append(args)
        return result(out="mouse-control\n")

    monkeypatch.setattr(updater, "_run", runner)
    installation = updater.detect_installation(Path("/usr/bin/mouse-control"))
    assert installation.package == "mouse-control"
    assert commands == [["rpm", "-qf", "--qf", "%{NAME}\\n", "/usr/bin/mouse-control"]]


def test_version_comparison_and_current_release(capsys, monkeypatch):
    install = updater.Installation("pip", Path("/tmp/mouse-control"), "mouse-control")
    monkeypatch.setattr(updater, "detect_installation", lambda _: install)
    monkeypatch.setattr(updater, "_shadowed", lambda _: None)
    assert updater.run_update(check=True, fetcher=lambda: release("v0.7.4")) == 0
    assert "already up to date" in capsys.readouterr().out


@pytest.mark.parametrize("kind,suffix,manager", [("rpm", ".rpm", "dnf"), ("deb", ".deb", "apt")])
def test_native_package_update_uses_manager_not_file_replacement(monkeypatch, kind, suffix, manager):
    install = updater.Installation(kind, Path("/usr/bin/mouse-control"), "mouse-control")
    monkeypatch.setattr(updater.shutil, "which", lambda command: f"/usr/bin/{command}")
    calls = []
    def runner(args):
        calls.append(args)
        return result(out="0.8.0\n") if args[0] in {"rpm", "dpkg-query"} else result()
    updater._package_update(install, release(assets=()), runner)
    assert calls and calls[0][0] in {"sudo", manager}
    assert not any(str(install.executable) in part for call in calls for part in call)


def test_package_manager_failure_uses_only_matching_official_asset(monkeypatch, tmp_path):
    install = updater.Installation("deb", Path("/usr/bin/mouse-control"), "mouse-control")
    asset = {"name": "mouse-control_0.8.0_all.deb", "browser_download_url": "https://github.com/DonGeronimo7/mouse-control/releases/download/v0.8.0/mouse-control_0.8.0_all.deb"}
    monkeypatch.setattr(updater.shutil, "which", lambda command: f"/usr/bin/{command}")
    def download(_asset, path):
        path.write_bytes(b"deb")
        return path
    monkeypatch.setattr(updater, "_download", download)
    calls = []
    def runner(args):
        calls.append(args)
        if args[0] == "dpkg-query":
            return result(out="0.8.0-1\n") if any(part.endswith(".deb") for part in calls[-2]) else result(out="0.7.6-1\n")
        return result(1 if len(calls) == 1 else 0)
    updater._package_update(install, release(assets=(asset,)), runner)
    assert any(any(part.endswith(".deb") for part in call) for call in calls)


@pytest.mark.parametrize("kind,asset_name,query", [
    ("rpm", "mouse-control-0.8.0-1.noarch.rpm", "rpm"),
    ("deb", "mouse-control_0.8.0_all.deb", "dpkg-query"),
])
def test_package_manager_false_success_falls_back_and_verifies(monkeypatch, kind, asset_name, query):
    install = updater.Installation(kind, Path("/usr/bin/mouse-control"), "mouse-control")
    asset = {"name": asset_name, "browser_download_url": f"https://github.com/DonGeronimo7/mouse-control/releases/download/v0.8.0/{asset_name}"}
    monkeypatch.setattr(updater.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(updater, "_download", lambda _asset, path: (path.write_bytes(b"package"), path)[1])
    calls, installed = [], ["0.7.6"]
    def runner(args):
        calls.append(args)
        if args[0] == query:
            suffix = "-1" if kind == "deb" else ""
            return result(out=installed[0] + suffix + "\n")
        if any(str(part).endswith((".rpm", ".deb")) for part in args):
            installed[0] = "0.8.0"
        return result()
    updater._package_update(install, release(assets=(asset,)), runner)
    assert any(any(str(part).endswith((".rpm", ".deb")) for part in call) for call in calls)


@pytest.mark.parametrize("kind,query_version", [("rpm", "0.8.0"), ("deb", "1:0.8.0-2")])
def test_package_manager_real_upgrade_does_not_download(monkeypatch, kind, query_version):
    install = updater.Installation(kind, Path("/usr/bin/mouse-control"), "mouse-control")
    monkeypatch.setattr(updater.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(updater, "_download", lambda *_args: pytest.fail("fallback must not download"))
    def runner(args):
        return result(out=query_version + "\n") if args[0] in {"rpm", "dpkg-query"} else result()
    updater._package_update(install, release(), runner)


def test_noisy_dnf_output_with_target_rpm_installed_reports_success(monkeypatch, capsys):
    installation = updater.Installation("rpm", Path("/usr/bin/mouse-control"), "mouse-control")
    monkeypatch.setattr(updater, "detect_installation", lambda _: installation)
    monkeypatch.setattr(updater, "_shadowed", lambda _: None)
    monkeypatch.setattr(updater, "is_service_active", lambda: False)
    monkeypatch.setattr(updater, "__version__", "0.7.9")
    monkeypatch.setattr(updater.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(updater, "_download", lambda *_: pytest.fail("fallback must not download"))
    calls = []

    def runner(args):
        calls.append(args)
        if args[0] == "rpm":
            assert args[-1] == "mouse-control"
            return result(out="0.7.10\n")
        return result(1, out="Last metadata expiration check...\nWarnings and progress text",
                      err="Warning: unrelated repository metadata")

    assert updater.run_update(assume_yes=True, fetcher=lambda: release("0.7.10"), runner=runner) == 0
    assert "updated successfully to 0.7.10" in capsys.readouterr().out
    assert "--assumeyes" in calls[0]


@pytest.mark.parametrize("assume_yes", [False, True])
def test_rpm_fallback_preserves_version_check_and_dnf_confirmation(monkeypatch, assume_yes):
    installation = updater.Installation("rpm", Path("/usr/bin/mouse-control"), "mouse-control")
    package = "mouse-control-0.8.0-1.noarch.rpm"
    artifact = {"name": package, "browser_download_url":
                f"https://github.com/DonGeronimo7/mouse-control/releases/download/v0.8.0/{package}"}
    monkeypatch.setattr(updater.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(updater, "_download", lambda _asset, path: (path.write_bytes(b"package"), path)[1])
    calls, installed = [], ["0.7.9"]

    def runner(args):
        calls.append(args)
        if args[0] == "rpm":
            return result(out=installed[0] + "\n")
        if any(str(part).endswith(".rpm") for part in args):
            installed[0] = "0.8.0"
            return result(1, out="Warnings and noisy DNF output", err="Warning: repository metadata")
        return result(out="Nothing to do")

    updater._package_update(installation, release(assets=(artifact,)), runner, assume_yes=assume_yes)
    dnf_calls = [args for args in calls if "dnf" in args]
    assert len(dnf_calls) == 2
    assert all(("--assumeyes" in args) is assume_yes for args in dnf_calls)
    assert dnf_calls[0][-1] == "mouse-control"
    assert dnf_calls[1][-1].endswith(".rpm")


def test_dnf_error_continues_to_verified_github_rpm(monkeypatch):
    install = updater.Installation("rpm", Path("/usr/bin/mouse-control"), "mouse-control")
    package = "mouse-control-0.8.0-1.noarch.rpm"
    artifact = {"name": package, "browser_download_url": f"https://github.com/DonGeronimo7/mouse-control/releases/download/v0.8.0/{package}"}
    monkeypatch.setattr(updater.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(updater, "_download", lambda _asset, path: (path.write_bytes(b"package"), path)[1])
    installed, calls = ["0.7.6"], []
    def runner(args):
        calls.append(args)
        if args[0] == "rpm":
            return result(out=installed[0] + "\n")
        if any(str(part).endswith(".rpm") for part in args):
            installed[0] = "0.8.0"
            return result()
        return result(1, err="not in repository")
    updater._package_update(install, release(assets=(artifact,)), runner)
    assert any(any(str(part).endswith(".rpm") for part in call) for call in calls)


@pytest.mark.parametrize("kind,asset_name,query", [
    ("rpm", "mouse-control-0.8.0-1.noarch.rpm", "rpm"),
    ("deb", "mouse-control_0.8.0_all.deb", "dpkg-query"),
])
def test_package_fallback_requires_final_version_verification(monkeypatch, kind, asset_name, query):
    install = updater.Installation(kind, Path("/usr/bin/mouse-control"), "mouse-control")
    asset = {"name": asset_name, "browser_download_url": f"https://github.com/DonGeronimo7/mouse-control/releases/download/v0.8.0/{asset_name}"}
    monkeypatch.setattr(updater.shutil, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(updater, "_download", lambda _asset, path: (path.write_bytes(b"package"), path)[1])
    def runner(args):
        return result(out="0.7.6\n") if args[0] == query else result()
    with pytest.raises(updater.UpdateError, match="did not install"):
        updater._package_update(install, release(assets=(asset,)), runner)


def test_arch_is_conservative():
    with pytest.raises(updater.UpdateError, match="not updated automatically"):
        updater._package_update(updater.Installation("arch", Path("/usr/bin/mouse-control")), release())


def test_appimage_replaces_atomically(monkeypatch, tmp_path):
    target = tmp_path / "Mouse-Control.AppImage"
    target.write_bytes(b"old")
    target.chmod(0o755)
    asset = {"name": "Mouse-Control-0.8.0-x86_64.AppImage", "browser_download_url": "https://github.com/DonGeronimo7/mouse-control/releases/download/v0.8.0/Mouse-Control-0.8.0-x86_64.AppImage"}
    monkeypatch.setattr(updater, "_architecture", lambda: "x86_64")
    monkeypatch.setattr(updater, "_download", lambda a, path, o: path.write_bytes(b"new") or path)
    updater._appimage_update(updater.Installation("appimage", target), release(assets=(asset,)))
    assert target.read_bytes() == b"new" and target.stat().st_mode & 0o111


def test_appimage_uses_secure_same_directory_staging_and_replaces_after_download(monkeypatch, tmp_path):
    target = tmp_path / "Mouse-Control.AppImage"
    target.write_bytes(b"old")
    target.chmod(0o755)
    monkeypatch.setattr(updater, "_architecture", lambda: "x86_64")
    downloaded = []
    def download(_asset, destination, _opener):
        downloaded.append(destination)
        assert destination.parent == target.parent
        assert destination.name != f".{target.name}.new"
        destination.write_bytes(b"new")
        return destination
    replaced = []
    original_replace = updater.os.replace
    def replace(source, destination):
        replaced.append((Path(source), Path(destination)))
        assert Path(source).read_bytes() == b"new"
        assert target.read_bytes() == b"old"
        original_replace(source, destination)
    monkeypatch.setattr(updater, "_download", download)
    monkeypatch.setattr(updater.os, "replace", replace)
    updater._appimage_update(updater.Installation("appimage", target), release(assets=(asset("Mouse-Control-0.8.0-x86_64.AppImage"),)))
    assert len(downloaded) == len(replaced) == 1
    assert target.read_bytes() == b"new"


def test_appimage_download_failure_preserves_target_and_removes_secure_staging(monkeypatch, tmp_path):
    target = tmp_path / "Mouse-Control.AppImage"
    target.write_bytes(b"old")
    monkeypatch.setattr(updater, "_architecture", lambda: "x86_64")
    staged = []
    def failed_download(_asset, destination, _opener):
        staged.append(destination)
        raise updater.UpdateError("offline")
    monkeypatch.setattr(updater, "_download", failed_download)
    with pytest.raises(updater.UpdateError, match="offline"):
        updater._appimage_update(updater.Installation("appimage", target), release(assets=(asset("Mouse-Control-0.8.0-x86_64.AppImage"),)))
    assert target.read_bytes() == b"old"
    assert staged and staged[0].parent == target.parent and not staged[0].exists()


def test_editable_and_unknown_are_never_updated(monkeypatch, capsys):
    for kind in ("source", "unknown"):
        install = updater.Installation(kind, Path("/tmp/mouse-control"))
        monkeypatch.setattr(updater, "detect_installation", lambda _, value=install: value)
        monkeypatch.setattr(updater, "_shadowed", lambda _: None)
        assert updater.run_update(check=False, assume_yes=True, fetcher=lambda: release("0.8.3")) == 1
    assert "cannot be updated automatically" in capsys.readouterr().err


def test_shadowed_copy_is_reported(monkeypatch, capsys):
    install = updater.Installation("pip", Path("/home/me/.local/bin/mouse-control"), "mouse-control")
    monkeypatch.setattr(updater, "detect_installation", lambda _: install)
    monkeypatch.setattr(updater, "_shadowed", lambda _: Path("/usr/bin/mouse-control"))
    updater.run_update(check=True, fetcher=lambda: release())
    assert "installed more than once" in capsys.readouterr().out


def test_check_mode_never_invokes_mutation_paths(monkeypatch):
    install = updater.Installation("pip", Path("/tmp/mouse-control"), "mouse-control")
    monkeypatch.setattr(updater, "detect_installation", lambda _: install)
    monkeypatch.setattr(updater, "_shadowed", lambda _: None)
    def fail(*_args, **_kwargs):
        raise AssertionError("mutation path was called during --check")
    monkeypatch.setattr(updater, "_package_update", fail)
    monkeypatch.setattr(updater, "_appimage_update", fail)
    monkeypatch.setattr(updater, "_download", fail)
    monkeypatch.setattr(updater, "is_service_active", fail)
    monkeypatch.setattr(updater, "restart_service", fail)
    monkeypatch.setattr(updater.os, "replace", fail)
    assert updater.run_update(check=True, fetcher=lambda: release(), runner=fail) == 0


def test_malformed_and_network_release_responses_fail_safely():
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b"[]"
    with pytest.raises(updater.UpdateError):
        updater.fetch_latest(opener=lambda *args, **kwargs: Response())
    with pytest.raises(updater.UpdateError):
        updater.fetch_latest(opener=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")))


def test_unsafe_or_ambiguous_assets_are_rejected():
    unsafe = {"name": "mouse-control_0.8.0_all.deb", "browser_download_url": "https://example.test/a.deb"}
    with pytest.raises(updater.UpdateError, match="official"):
        updater.select_asset(release(assets=(unsafe,)), ".deb")
    with pytest.raises(updater.UpdateError, match="identify one"):
        updater.select_asset(release(assets=(unsafe, unsafe)), ".deb")


def test_successful_update_restarts_only_active_service(monkeypatch):
    install = updater.Installation("pip", Path("/tmp/mouse-control"), "mouse-control")
    monkeypatch.setattr(updater, "detect_installation", lambda _: install)
    monkeypatch.setattr(updater, "_shadowed", lambda _: None)
    monkeypatch.setattr(updater, "is_service_active", lambda: True)
    restarted = []
    monkeypatch.setattr(updater, "restart_service", lambda: restarted.append(True))
    assert updater.run_update(assume_yes=True, fetcher=lambda: release("0.8.3"), runner=lambda _: result()) == 0
    assert restarted == [True]
    monkeypatch.setattr(updater, "is_service_active", lambda: False)
    assert updater.run_update(assume_yes=True, fetcher=lambda: release("0.8.3"), runner=lambda _: result()) == 0
    assert restarted == [True]


def test_failed_service_restart_does_not_claim_install_failed(monkeypatch, capsys):
    install = updater.Installation("pip", Path("/tmp/mouse-control"), "mouse-control")
    monkeypatch.setattr(updater, "detect_installation", lambda _: install)
    monkeypatch.setattr(updater, "_shadowed", lambda _: None)
    monkeypatch.setattr(updater, "is_service_active", lambda: True)
    monkeypatch.setattr(updater, "restart_service", lambda: (_ for _ in ()).throw(OSError("no bus")))
    assert updater.run_update(assume_yes=True, fetcher=lambda: release("0.8.3"), runner=lambda _: result()) == 1
    output = capsys.readouterr()
    assert "updated successfully" in output.out and "could not be restarted" in output.err
