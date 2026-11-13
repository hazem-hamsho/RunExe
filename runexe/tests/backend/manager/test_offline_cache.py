import tarfile
import zipfile
from hashlib import md5, sha256
from pathlib import Path
from types import SimpleNamespace

from runexe.backend.globals import Paths
from runexe.backend.managers import component as component_module
from runexe.backend.managers import dependency as dependency_module
from runexe.backend.managers.component import ComponentManager
from runexe.backend.managers.dependency import DependencyManager
from runexe.backend.models.config import CapsuleConfig
from runexe.backend.repos.component import ComponentRepo
from runexe.backend.repos.dependency import DependencyRepo
from runexe.backend.state import TaskManager


def test_component_manager_extracts_zip_archive(tmp_path, monkeypatch):
    temp_path = tmp_path / "temp"
    dxvk_path = tmp_path / "dxvk"
    temp_path.mkdir()
    dxvk_path.mkdir()
    archive_path = temp_path / "dxvk-v2.0.zip"

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dxvk-v2.0/x64/d3d11.dll", b"dxvk")

    monkeypatch.setattr(Paths, "temp", str(temp_path))
    monkeypatch.setattr(Paths, "dxvk", str(dxvk_path))

    assert ComponentManager.extract("dxvk-v2.0", "dxvk", archive_path.name)
    assert (dxvk_path / "dxvk-v2.0/x64/d3d11.dll").read_bytes() == b"dxvk"


def test_component_manager_rejects_zip_path_traversal(tmp_path, monkeypatch):
    temp_path = tmp_path / "temp"
    dxvk_path = tmp_path / "dxvk"
    temp_path.mkdir()
    dxvk_path.mkdir()
    sentinel = dxvk_path / "dxvk-v2.0" / "keep"
    sentinel.parent.mkdir()
    sentinel.write_text("safe")
    archive_path = temp_path / "dxvk-v2.0.zip"

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escape.dll", b"invalid")

    monkeypatch.setattr(Paths, "temp", str(temp_path))
    monkeypatch.setattr(Paths, "dxvk", str(dxvk_path))

    assert not ComponentManager.extract("dxvk-v2.0", "dxvk", archive_path.name)
    assert not (tmp_path / "escape.dll").exists()
    assert sentinel.read_text() == "safe"


def test_component_manager_rejects_zip_with_another_root(tmp_path, monkeypatch):
    temp_path = tmp_path / "temp"
    dxvk_path = tmp_path / "dxvk"
    preserved = dxvk_path / "dxvk-v1.0/x64/d3d11.dll"
    temp_path.mkdir()
    preserved.parent.mkdir(parents=True)
    preserved.write_bytes(b"installed")
    archive_path = temp_path / "dxvk-v2.0.zip"

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dxvk-v2.0/x64/d3d11.dll", b"expected")
        archive.writestr("dxvk-v1.0/x64/d3d11.dll", b"overwrite")

    monkeypatch.setattr(Paths, "temp", str(temp_path))
    monkeypatch.setattr(Paths, "dxvk", str(dxvk_path))

    assert not ComponentManager.extract("dxvk-v2.0", "dxvk", archive_path.name)
    assert preserved.read_bytes() == b"installed"
    assert not (dxvk_path / "dxvk-v2.0").exists()


def test_component_manager_handles_zip_root_as_file(tmp_path, monkeypatch):
    temp_path = tmp_path / "temp"
    dxvk_path = tmp_path / "dxvk"
    temp_path.mkdir()
    dxvk_path.mkdir()
    archive_path = temp_path / "dxvk-v2.0.zip"

    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("dxvk-v2.0", b"not a directory")

    monkeypatch.setattr(Paths, "temp", str(temp_path))
    monkeypatch.setattr(Paths, "dxvk", str(dxvk_path))

    assert not ComponentManager.extract("dxvk-v2.0", "dxvk", archive_path.name)
    assert not (dxvk_path / "dxvk-v2.0").exists()


def test_repository_reuses_catalog_and_manifest_offline(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    catalog_path = source / "index.yml"
    manifest_path = source / "example.yml"
    catalog_path.write_text("example:\n  Category: misc\n")
    manifest_path.write_text("Name: example\nSteps: []\n")
    monkeypatch.setattr(Paths, "temp", str(tmp_path / "cache"))

    repo = object.__new__(DependencyRepo)
    repo.url = "https://example.invalid/dependencies/"
    repo.offline = False

    catalog = repo._Repo__get_catalog(catalog_path.as_uri())
    manifest = repo.get_manifest(manifest_path.as_uri())

    catalog_path.unlink()
    manifest_path.unlink()

    assert repo._Repo__get_catalog(catalog_path.as_uri()) == catalog
    assert repo.get_manifest(manifest_path.as_uri()) == manifest

    repo.offline = True

    assert repo._Repo__get_catalog("") == catalog
    assert repo.get_manifest(manifest_path.as_uri()) == manifest
    assert "Name: example" in repo.get_manifest(manifest_path.as_uri(), plain=True)


def test_repository_rejects_invalid_cached_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr(Paths, "temp", str(tmp_path))
    repo = object.__new__(DependencyRepo)
    repo.url = "https://example.invalid/dependencies/"
    repo.offline = True

    catalog_path = repo._Repo__get_cache_path("catalog.yml")
    manifest_url = "https://example.invalid/dependencies/Misc/example.yml"
    manifest_name = sha256(manifest_url.encode()).hexdigest()
    manifest_path = repo._Repo__get_cache_path(f"{manifest_name}.yml")
    for path in (catalog_path, manifest_path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("- invalid\n")

    assert repo._Repo__get_catalog("") == {}
    assert repo.get_manifest(manifest_url) is False
    assert repo.get_manifest(manifest_url, plain=True) is False


def test_repository_rejects_invalid_catalog_entries():
    dependency_repo = object.__new__(DependencyRepo)
    dependency_repo.url = "https://example.invalid/dependencies/"
    dependency_repo.catalog = {"empty": {}, "scalar": "invalid"}
    component_repo = object.__new__(ComponentRepo)
    component_repo.url = "https://example.invalid/components/"
    component_repo.catalog = {
        "empty": {},
        "scalar": "invalid",
        "bad-subcategory": {"Category": "runners", "Sub-category": []},
    }

    assert dependency_repo.get("empty") is False
    assert dependency_repo.get("scalar") is False
    assert dependency_repo.get(1) is False  # type: ignore[arg-type]
    assert component_repo.get("empty") is False
    assert component_repo.get("scalar") is False
    assert component_repo.get("bad-subcategory") is False
    assert component_repo.get(1) is False  # type: ignore[arg-type]


def test_dependency_catalog_handles_invalid_nested_entry():
    catalog = {
        "broken": {},
        "scalar": "invalid",
        "missing-description": {"Category": "Misc"},
        "bad-arch": {
            "Category": "Misc",
            "Description": "Invalid",
            "Arch": {},
        },
        1: {"Category": "Misc", "Description": "Invalid"},
    }
    repo = object.__new__(DependencyRepo)
    repo.url = "https://example.invalid/dependencies/"
    repo.offline = True
    repo.catalog = catalog
    dependency_manager = object.__new__(DependencyManager)
    dependency_manager._DependencyManager__manifests = {}
    dependency_manager._DependencyManager__repo = repo
    dependency_manager._DependencyManager__offline = True
    dependency_manager._DependencyManager__checksum_cache = {}

    result = dependency_manager.fetch_catalog()

    assert result == {}


def test_component_cache_requires_manifest_and_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(Paths, "temp", str(tmp_path))
    (tmp_path / "cached.tar.xz").write_bytes(b"runner")

    manifests = {
        "cached": {
            "File": [
                {
                    "url": "https://example.invalid/cached.tar.xz",
                    "file_name": "cached.tar.xz",
                    "rename": "",
                }
            ]
        },
        "missing": {
            "File": [
                {
                    "url": "https://example.invalid/missing.tar.xz",
                    "file_name": "missing.tar.xz",
                    "rename": "",
                }
            ]
        },
    }
    repo = SimpleNamespace(get=lambda name, plain=False: manifests.get(name, False))
    component_manager = object.__new__(ComponentManager)
    component_manager._ComponentManager__manifests = {}
    component_manager._ComponentManager__repo = repo

    assert component_manager.is_component_cached("cached") is True
    assert component_manager.is_component_cached("missing") is False
    assert component_manager.is_component_cached("unknown") is False


def test_component_cache_rejects_corrupt_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(Paths, "temp", str(tmp_path))
    payload = tmp_path / "cached.tar.xz"
    payload.write_bytes(b"corrupt")
    manifest = {
        "File": [
            {
                "url": "https://example.invalid/cached.tar.xz",
                "file_name": payload.name,
                "file_checksum": md5(b"valid").hexdigest(),
            }
        ]
    }
    component_manager = object.__new__(ComponentManager)
    component_manager._ComponentManager__manifests = {}
    component_manager._ComponentManager__repo = SimpleNamespace(
        get=lambda name, plain=False: manifest
    )
    component_manager._ComponentManager__checksum_cache = {}

    assert component_manager.is_component_cached("cached") is False
    payload.write_bytes(b"valid")
    assert component_manager.is_component_cached("cached") is True


def test_component_catalog_is_available_without_connection():
    catalog = {
        "test-runner": {
            "Category": "runners",
            "Sub-category": "wine",
            "Channel": "stable",
        },
        "missing-channel": {"Category": "dxvk"},
        "bad-category": {"Category": [], "Channel": "stable"},
        "bad-runner": {
            "Category": "runners",
            "Sub-category": [],
            "Channel": "stable",
        },
        1: {"Category": "dxvk", "Channel": "stable"},
    }
    manager = SimpleNamespace(
        runtimes_available=[],
        runners_available=[],
        dxvk_available=[],
        vkd3d_available=[],
        winebridge_available=[],
    )
    component_manager = object.__new__(ComponentManager)
    component_manager._ComponentManager__manifests = {}
    component_manager._ComponentManager__manager = manager
    component_manager._ComponentManager__repo = SimpleNamespace(catalog=catalog)

    assert component_manager.fetch_catalog()["wine"] == {
        "test-runner": catalog["test-runner"]
    }


def test_component_catalog_filters_host_platform_and_architecture(monkeypatch):
    catalog = {
        "linux-arm-runner": {
            "Category": "runners",
            "Sub-category": "wine",
            "Channel": "stable",
            "Platforms": ["linux"],
            "Architectures": ["aarch64"],
        },
        "mac-arm-runner": {
            "Category": "runners",
            "Sub-category": "wine",
            "Channel": "stable",
            "Platforms": ["darwin"],
            "Architectures": ["aarch64"],
        },
        "linux-x86-runner": {
            "Category": "runners",
            "Sub-category": "wine",
            "Channel": "stable",
            "Platforms": ["linux"],
            "Architectures": ["x86_64"],
        },
    }
    manager = SimpleNamespace(
        runtimes_available=[],
        runners_available=[],
        dxvk_available=[],
        vkd3d_available=[],
        winebridge_available=[],
    )
    component_manager = object.__new__(ComponentManager)
    component_manager._ComponentManager__manifests = {}
    component_manager._ComponentManager__manager = manager
    component_manager._ComponentManager__repo = SimpleNamespace(catalog=catalog)
    monkeypatch.setattr(
        component_module, "get_host_architecture", lambda: "aarch64"
    )
    monkeypatch.setattr(component_module.sys, "platform", "linux")

    assert component_manager.fetch_catalog()["wine"] == {
        "linux-arm-runner": catalog["linux-arm-runner"]
    }


def test_component_catalog_marks_checksum_valid_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Paths, "temp", str(tmp_path))
    payload = tmp_path / "cached.tar.xz"
    payload.write_bytes(b"runner")
    catalog = {
        "cached": {
            "Category": "runners",
            "Sub-category": "wine",
            "Channel": "stable",
        },
        "missing": {
            "Category": "runners",
            "Sub-category": "wine",
            "Channel": "stable",
        },
    }
    manifests = {
        name: {
            "File": [
                {
                    "url": f"https://example.invalid/{name}.tar.xz",
                    "file_name": f"{name}.tar.xz",
                    "file_checksum": md5(b"runner").hexdigest(),
                }
            ]
        }
        for name in catalog
    }
    manager = SimpleNamespace(
        runtimes_available=[],
        runners_available=[],
        dxvk_available=[],
        vkd3d_available=[],
        winebridge_available=[],
    )
    component_manager = object.__new__(ComponentManager)
    component_manager._ComponentManager__manifests = {}
    component_manager._ComponentManager__manager = manager
    component_manager._ComponentManager__repo = SimpleNamespace(
        catalog=catalog,
        get=lambda name, plain=False: manifests.get(name, False),
    )
    component_manager._ComponentManager__offline = True
    component_manager._ComponentManager__checksum_cache = {}

    wine = component_manager.fetch_catalog()["wine"]

    assert wine["cached"]["Cached"] is True
    assert wine["missing"]["Cached"] is False


def test_cached_component_installs_without_network(tmp_path, monkeypatch):
    temp = tmp_path / "temp"
    runners = tmp_path / "runners"
    payload = tmp_path / "payload" / "test-runner" / "bin"
    temp.mkdir()
    runners.mkdir()
    payload.mkdir(parents=True)
    (payload / "wine").write_text("runner")
    archive = temp / "test-runner.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload.parent, arcname="test-runner")

    monkeypatch.setattr(Paths, "temp", str(temp))
    monkeypatch.setattr(Paths, "runners", str(runners))
    manifest = {
        "File": [
            {
                "url": "https://example.invalid/test-runner.tar.gz",
                "file_name": archive.name,
                "rename": "",
                "file_checksum": "",
            }
        ]
    }
    manager = SimpleNamespace(
        check_app_dirs=lambda: None,
        check_runners=lambda: None,
        organize_components=lambda: None,
    )
    component_manager = object.__new__(ComponentManager)
    component_manager._ComponentManager__manifests = {}
    component_manager._ComponentManager__manager = manager
    component_manager._ComponentManager__repo = SimpleNamespace(
        get=lambda name, plain=False: manifest
    )
    tasks_before = set(TaskManager._TASKS)

    result = component_manager.install("runner", "test-runner")

    assert result.ok
    assert (runners / "test-runner" / "bin" / "wine").read_text() == "runner"
    assert set(TaskManager._TASKS) == tasks_before


def test_offline_download_never_uses_network(tmp_path, monkeypatch):
    monkeypatch.setattr(Paths, "temp", str(tmp_path))
    monkeypatch.setattr(
        component_module,
        "Downloader",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("network access attempted")),
    )
    component_manager = object.__new__(ComponentManager)
    component_manager._ComponentManager__manifests = {}
    component_manager._ComponentManager__manager = SimpleNamespace(
        check_app_dirs=lambda: None
    )
    component_manager._ComponentManager__offline = True
    component_manager._ComponentManager__checksum_cache = {}
    tasks_before = set(TaskManager._TASKS)

    missing = component_manager.download(
        "https://example.invalid/missing.exe", "missing.exe"
    )
    corrupt_path = tmp_path / "corrupt.exe"
    corrupt_path.write_bytes(b"corrupt")
    corrupt = component_manager.download(
        "https://example.invalid/corrupt.exe",
        "corrupt.exe",
        checksum=md5(b"valid").hexdigest(),
    )

    assert not missing.ok
    assert missing.message == "File is not available in offline mode."
    assert not corrupt.ok
    assert not corrupt_path.exists()
    assert set(TaskManager._TASKS) == tasks_before


def test_dependency_cache_checks_relevant_architecture(tmp_path, monkeypatch):
    monkeypatch.setattr(Paths, "temp", str(tmp_path))
    (tmp_path / "win64.exe").write_bytes(b"installer")

    manifests = {
        "example": {
            "Steps": [
                {
                    "action": "install_exe",
                    "for": "win64",
                    "url": "https://example.invalid/win64.exe",
                    "file_name": "win64.exe",
                },
                {
                    "action": "install_exe",
                    "for": "win32",
                    "url": "https://example.invalid/win32.exe",
                    "file_name": "win32.exe",
                },
            ]
        }
    }
    repo = SimpleNamespace(get=lambda name, plain=False: manifests.get(name, False))
    dependency_manager = object.__new__(DependencyManager)
    dependency_manager._DependencyManager__manifests = {}
    dependency_manager._DependencyManager__repo = repo

    assert dependency_manager.is_dependency_cached("example", "win64") is True
    assert dependency_manager.is_dependency_cached("example", "win32") is False


def test_dependency_cache_requires_cached_prerequisites(tmp_path, monkeypatch):
    monkeypatch.setattr(Paths, "temp", str(tmp_path))
    (tmp_path / "main.exe").write_bytes(b"installer")

    manifests = {
        "main": {
            "Dependencies": ["prerequisite"],
            "Steps": [
                {
                    "action": "install_exe",
                    "url": "https://example.invalid/main.exe",
                    "file_name": "main.exe",
                }
            ],
        },
        "prerequisite": {
            "Steps": [
                {
                    "action": "install_exe",
                    "url": "https://example.invalid/prerequisite.exe",
                    "file_name": "prerequisite.exe",
                }
            ]
        },
    }
    repo = SimpleNamespace(get=lambda name, plain=False: manifests.get(name, False))
    dependency_manager = object.__new__(DependencyManager)
    dependency_manager._DependencyManager__manifests = {}
    dependency_manager._DependencyManager__repo = repo

    assert dependency_manager.is_dependency_cached("main", "win64") is False
    (tmp_path / "prerequisite.exe").write_bytes(b"installer")
    assert dependency_manager.is_dependency_cached("main", "win64") is True


def test_dependency_cycle_is_rejected_without_recursion(monkeypatch):
    manifests = {
        "first": {"Dependencies": ["second"], "Steps": []},
        "second": {"Dependencies": ["first"], "Steps": []},
    }
    repo = SimpleNamespace(get=lambda name, plain=False: manifests.get(name, False))
    manager = SimpleNamespace(
        supported_dependencies={name: {} for name in manifests},
    )
    dependency_manager = object.__new__(DependencyManager)
    dependency_manager._DependencyManager__manifests = {}
    dependency_manager._DependencyManager__manager = manager
    dependency_manager._DependencyManager__repo = repo
    dependency_manager._DependencyManager__checksum_cache = {}
    config = CapsuleConfig(Name="Offline")
    tasks_before = set(TaskManager._TASKS)

    assert dependency_manager.is_dependency_cached("first", "win64") is False
    assert (
        dependency_manager._DependencyManager__dependency_has_cycle(
            "first", installed=["second"]
        )
        is False
    )
    result = dependency_manager.install(config, ["first", {}])

    assert not result.ok
    assert result.message == "Cyclic dependency chain for first."
    assert set(TaskManager._TASKS) == tasks_before


def test_dependency_rejects_incomplete_manifest():
    manifest = {"Dependencies": [], "Steps": None}
    dependency_manager = object.__new__(DependencyManager)
    dependency_manager._DependencyManager__manifests = {}
    dependency_manager._DependencyManager__manager = SimpleNamespace()
    dependency_manager._DependencyManager__repo = SimpleNamespace(
        get=lambda name, plain=False: manifest
    )
    dependency_manager._DependencyManager__offline = True
    dependency_manager._DependencyManager__checksum_cache = {}
    config = CapsuleConfig(Name="Offline")
    tasks_before = set(TaskManager._TASKS)

    assert dependency_manager.is_dependency_cached("broken", "win64") is False
    result = dependency_manager.install(config, ["broken", {}])

    assert not result.ok
    assert result.message == "Invalid manifest for broken."
    assert set(TaskManager._TASKS) == tasks_before


def test_dependency_catalog_is_available_without_connection():
    catalog = {"example": {"Category": "Misc", "Description": "Example"}}
    dependency_manager = object.__new__(DependencyManager)
    dependency_manager._DependencyManager__manifests = {}
    dependency_manager._DependencyManager__repo = SimpleNamespace(catalog=catalog)

    assert dependency_manager.fetch_catalog() == catalog


def test_dependency_catalog_marks_architecture_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(Paths, "temp", str(tmp_path))
    payload = tmp_path / "win64.exe"
    payload.write_bytes(b"installer")
    catalog = {"example": {"Category": "Misc", "Description": "Example"}}
    manifest = {
        "Steps": [
            {
                "action": "install_exe",
                "for": "win64",
                "url": "https://example.invalid/win64.exe",
                "file_name": payload.name,
                "file_checksum": md5(b"installer").hexdigest(),
            },
            {
                "action": "install_exe",
                "for": "win32",
                "url": "https://example.invalid/win32.exe",
                "file_name": "win32.exe",
                "file_checksum": md5(b"installer").hexdigest(),
            },
        ]
    }
    dependency_manager = object.__new__(DependencyManager)
    dependency_manager._DependencyManager__manifests = {}
    dependency_manager._DependencyManager__repo = SimpleNamespace(
        catalog=catalog,
        get=lambda name, plain=False: manifest,
    )
    dependency_manager._DependencyManager__offline = True
    dependency_manager._DependencyManager__checksum_cache = {}

    cached = dependency_manager.fetch_catalog()["example"]["Cached"]

    assert cached == {"win32": False, "win64": True}


def test_cached_dependency_installs_without_network(tmp_path, monkeypatch):
    temp = tmp_path / "temp"
    temp.mkdir()
    archive = temp / "example.zip"
    archive.write_bytes(b"archive")
    monkeypatch.setattr(Paths, "temp", str(temp))
    monkeypatch.setattr(
        dependency_module.RegistryRuleManager,
        "apply_rules",
        lambda *_args, **_kwargs: None,
    )

    manifest = {
        "Steps": [
            {
                "action": "download_archive",
                "url": "https://example.invalid/example.zip",
                "file_name": archive.name,
                "rename": "",
                "file_checksum": "",
            }
        ]
    }
    component_manager = object.__new__(ComponentManager)
    component_manager._ComponentManager__manifests = {}
    component_manager._ComponentManager__manager = SimpleNamespace(
        check_app_dirs=lambda: None
    )
    manager = SimpleNamespace(
        component_manager=component_manager,
        supported_dependencies={},
        update_config=lambda *_args, **_kwargs: None,
    )
    dependency_manager = object.__new__(DependencyManager)
    dependency_manager._DependencyManager__manifests = {}
    dependency_manager._DependencyManager__manager = manager
    dependency_manager._DependencyManager__repo = SimpleNamespace(
        get=lambda name, plain=False: manifest
    )
    dependency_manager._DependencyManager__offline = True
    dependency_manager._DependencyManager__checksum_cache = {}
    config = CapsuleConfig(Name="Offline")
    tasks_before = set(TaskManager._TASKS)

    result = dependency_manager.install(config, ["example", {}])

    assert result.ok
    assert set(TaskManager._TASKS) == tasks_before


def test_missing_offline_dependency_does_not_use_network(tmp_path, monkeypatch):
    monkeypatch.setattr(Paths, "temp", str(tmp_path))
    monkeypatch.setattr(
        component_module,
        "Downloader",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("network access attempted")),
    )
    manifest = {
        "Steps": [
            {
                "action": "download_archive",
                "url": "https://example.invalid/missing.zip",
                "file_name": "missing.zip",
            }
        ]
    }
    component_manager = object.__new__(ComponentManager)
    component_manager._ComponentManager__manifests = {}
    component_manager._ComponentManager__manager = SimpleNamespace(
        check_app_dirs=lambda: None
    )
    component_manager._ComponentManager__offline = True
    component_manager._ComponentManager__checksum_cache = {}
    manager = SimpleNamespace(
        component_manager=component_manager,
        supported_dependencies={},
    )
    dependency_manager = object.__new__(DependencyManager)
    dependency_manager._DependencyManager__manifests = {}
    dependency_manager._DependencyManager__manager = manager
    dependency_manager._DependencyManager__repo = SimpleNamespace(
        get=lambda name, plain=False: manifest
    )
    dependency_manager._DependencyManager__offline = True
    dependency_manager._DependencyManager__checksum_cache = {}
    config = CapsuleConfig(Name="Offline")
    tasks_before = set(TaskManager._TASKS)

    result = dependency_manager.install(config, ["missing", {}])

    assert not result.ok
    assert result.message == "Files for missing are not available in offline mode."
    assert set(TaskManager._TASKS) == tasks_before
