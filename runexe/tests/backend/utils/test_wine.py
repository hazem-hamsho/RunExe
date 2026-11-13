import os

import pytest

from runexe.backend.utils import wine as wine_module
from runexe.backend.utils.wine import WineUtils


@pytest.mark.parametrize("arch", ("win32", "win64"))
def test_reads_prefix_architecture_before_registry_sections(tmp_path, arch):
    (tmp_path / "system.reg").write_text(
        f"WINE REGISTRY Version 2\n;; Test fixture\n#arch={arch}\n\n[Software]"
    )
    assert WineUtils.get_prefix_arch(str(tmp_path)) == arch


@pytest.mark.parametrize("layout", ("", "files", "dist"))
@pytest.mark.parametrize("library", ("lib", "lib64", "lib/x86_64-linux-gnu"))
def test_distinguishes_wow64_only_from_dual_unix_runner(tmp_path, layout, library):
    wine = tmp_path / layout / library / "wine"
    unix64 = wine / "x86_64-unix/ntdll.so"
    unix64.parent.mkdir(parents=True)
    unix64.write_bytes(b"64-bit Unix fixture")
    pe32 = wine / "i386-windows/ntdll.dll"
    pe32.parent.mkdir()
    pe32.write_bytes(b"32-bit PE support does not imply win32 prefix support")

    assert not WineUtils.supports_win32_prefix(str(tmp_path))

    unix32 = tmp_path / layout / "lib/wine/i386-unix/ntdll.so"
    unix32.parent.mkdir(parents=True, exist_ok=True)
    unix32.write_bytes(b"32-bit Unix fixture")
    assert WineUtils.supports_win32_prefix(str(tmp_path))


def test_unknown_runner_layout_is_not_rejected(tmp_path):
    assert WineUtils.supports_win32_prefix(str(tmp_path))


def test_rejects_linked_public_directory(tmp_path):
    prefix = tmp_path / "prefix"
    users = prefix / "drive_c" / "users"
    outside = tmp_path / "outside"
    users.mkdir(parents=True)
    outside.mkdir()
    (users / "Public").symlink_to(outside)

    assert WineUtils.ensure_user_profile_alias(str(prefix), "mirko") is False

    assert not (users / "mirko").exists()
    assert (users / "Public").resolve() == outside


def test_uses_user_environment_before_account_database(monkeypatch, tmp_path):
    prefix = tmp_path / "prefix"
    monkeypatch.setenv("USER", "environment-user")
    monkeypatch.setattr(
        wine_module.pwd,
        "getpwuid",
        lambda _uid: type("Account", (), {"pw_name": "account-user"})(),
    )

    assert WineUtils.ensure_user_profile_alias(str(prefix)) is True

    users = prefix / "drive_c" / "users"
    assert (users / "environment-user").is_dir()
    assert not (users / "account-user").exists()


def test_uses_account_database_when_user_environment_is_missing(monkeypatch, tmp_path):
    prefix = tmp_path / "prefix"
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.setattr(
        wine_module.pwd,
        "getpwuid",
        lambda _uid: type("Account", (), {"pw_name": "account-user"})(),
    )

    assert WineUtils.ensure_user_profile_alias(str(prefix)) is True

    users = prefix / "drive_c" / "users"
    assert (users / "account-user").is_dir()


def test_uses_wine_when_user_lookup_fails(monkeypatch, tmp_path):
    prefix = tmp_path / "prefix"
    monkeypatch.delenv("USER", raising=False)

    def raise_key_error(_uid):
        raise KeyError

    monkeypatch.setattr(wine_module.pwd, "getpwuid", raise_key_error)

    assert WineUtils.ensure_user_profile_alias(str(prefix)) is True

    users = prefix / "drive_c" / "users"
    assert (users / "wine").is_dir()


@pytest.mark.parametrize(
    ("username", "normalized"),
    [
        ("nested/user", "user"),
        ("../outside", "outside"),
        ("nested\\user", "user"),
        ("..\\outside", "outside"),
        ("nested/path\\user", "user"),
    ],
)
def test_normalizes_user_name_like_wine(tmp_path, username, normalized):
    prefix = tmp_path / "prefix"

    assert WineUtils.ensure_user_profile_alias(str(prefix), username) is True

    users = prefix / "drive_c" / "users"
    assert (users / normalized).is_dir()


def test_rolls_back_new_profile_when_alias_creation_fails(monkeypatch, tmp_path):
    prefix = tmp_path / "prefix"

    def fail_symlink(_target, _link):
        raise OSError

    monkeypatch.setattr(wine_module.os, "symlink", fail_symlink)

    assert WineUtils.ensure_user_profile_alias(str(prefix), "mirko") is False

    users = prefix / "drive_c" / "users"
    assert not (users / "mirko").exists()


def test_rolls_back_first_alias_when_second_alias_creation_fails(monkeypatch, tmp_path):
    prefix = tmp_path / "prefix"
    users = prefix / "drive_c" / "users"
    template_user = users / "template-user"
    template_user.mkdir(parents=True)
    marker = template_user / "save.dat"
    marker.write_text("save")
    symlink = wine_module.os.symlink
    calls = 0

    def fail_second_symlink(target, link):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError
        symlink(target, link)

    monkeypatch.setattr(wine_module.os, "symlink", fail_second_symlink)

    assert WineUtils.ensure_user_profile_alias(str(prefix), "mirko") is False

    assert not (users / "mirko").exists()
    assert marker.read_text() == "save"


def test_get_user_dir_prefers_real_profile_over_alias(tmp_path):
    prefix = tmp_path / "prefix"
    assert WineUtils.ensure_user_profile_alias(str(prefix), "mirko") is True

    assert WineUtils.get_user_dir(str(prefix)) == "mirko"


def test_get_user_dir_rejects_conflicting_profiles(tmp_path):
    users = tmp_path / "prefix" / "drive_c" / "users"
    (users / "zeta").mkdir(parents=True)
    (users / "alpha").mkdir()
    (users / "Public").mkdir()

    with pytest.raises(Exception, match="Multiple user directories"):
        WineUtils.get_user_dir(str(tmp_path / "prefix"))


@pytest.mark.parametrize(
    "username",
    [
        "",
        ".",
        "..",
        "Public",
        "PUBLIC",
        "nested/Public",
        "nested\\Public",
        "null\0user",
    ],
)
def test_rejects_unsafe_user_profile_names(tmp_path, username):
    prefix = tmp_path / "prefix"

    assert WineUtils.ensure_user_profile_alias(str(prefix), username) is False

    assert not (prefix / "drive_c" / "users").exists()


def test_unlinks_only_new_user_profile_links(tmp_path):
    prefix = tmp_path / "prefix"
    users = prefix / "drive_c" / "users"
    existing = users / "mirko"
    existing.mkdir(parents=True)
    existing_target = tmp_path / "existing-target"
    existing_target.mkdir()
    (existing / "Custom").symlink_to(existing_target)
    existing_profiles = WineUtils.get_user_profile_ids(str(prefix))

    created = users / "otheruser"
    created.mkdir()
    created_target = tmp_path / "created-target"
    created_target.mkdir()
    (created / "Documents").symlink_to(created_target)

    assert WineUtils.unlink_user_profile_links(str(prefix), existing_profiles) is True

    assert (existing / "Custom").is_symlink()
    assert (created / "Documents").is_dir()
    assert not (created / "Documents").is_symlink()


def test_unlinks_nested_shell_folder_links(tmp_path):
    prefix = tmp_path / "prefix"
    profile = prefix / "drive_c" / "users" / "otheruser"
    documents = profile / "Documents"
    windows = profile / "AppData" / "Roaming" / "Microsoft" / "Windows"
    documents.mkdir(parents=True)
    windows.mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    (documents / "My Music").symlink_to(target)
    (windows / "Templates").symlink_to(target)

    assert WineUtils.unlink_user_profile_links(str(prefix)) is True

    assert (documents / "My Music").is_dir()
    assert not (documents / "My Music").is_symlink()
    assert (windows / "Templates").is_dir()
    assert not (windows / "Templates").is_symlink()


def test_does_not_follow_profile_or_shell_folder_links(tmp_path):
    prefix = tmp_path / "prefix"
    users = prefix / "drive_c" / "users"
    users.mkdir(parents=True)
    outside_profile = tmp_path / "outside-profile"
    outside_profile.mkdir()
    outside_target = tmp_path / "outside-target"
    outside_target.mkdir()
    outside_link = outside_profile / "Documents"
    outside_link.symlink_to(outside_target)
    (users / "escaped").symlink_to(outside_profile)

    profile = users / "otheruser"
    profile.mkdir()
    host_documents = tmp_path / "host-documents"
    host_documents.mkdir()
    nested_link = host_documents / "keep-link"
    nested_link.symlink_to(outside_target)
    (profile / "Documents").symlink_to(host_documents)

    assert WineUtils.unlink_user_profile_links(str(prefix)) is True

    assert outside_link.is_symlink()
    assert nested_link.is_symlink()
    assert (users / "escaped").is_symlink()
    assert (profile / "Documents").is_dir()
    assert not (profile / "Documents").is_symlink()


def test_restores_link_when_directory_creation_fails(monkeypatch, tmp_path):
    prefix = tmp_path / "prefix"
    profile = prefix / "drive_c" / "users" / "otheruser"
    profile.mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    desktop = profile / "Desktop"
    desktop.symlink_to(target)
    mkdir = wine_module.os.mkdir

    def fail_desktop(path, *args, **kwargs):
        if path == "Desktop":
            raise OSError
        return mkdir(path, *args, **kwargs)

    monkeypatch.setattr(wine_module.os, "mkdir", fail_desktop)

    assert WineUtils.unlink_user_profile_links(str(prefix)) is False

    assert desktop.is_symlink()
    assert os.readlink(desktop) == str(target)


def test_unlinks_public_profile_links(tmp_path):
    prefix = tmp_path / "prefix"
    public = prefix / "drive_c" / "users" / "Public"
    public.mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    desktop = public / "Desktop"
    desktop.symlink_to(target)

    assert WineUtils.unlink_user_profile_links(str(prefix)) is True

    assert desktop.is_dir()
    assert not desktop.is_symlink()


def test_rejects_symlinked_drive_c(tmp_path):
    prefix = tmp_path / "prefix"
    outside = tmp_path / "outside"
    profile = outside / "users" / "otheruser"
    profile.mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    desktop = profile / "Desktop"
    desktop.symlink_to(target)
    prefix.mkdir()
    (prefix / "drive_c").symlink_to(outside)

    assert WineUtils.get_user_profile_ids(str(prefix)) is None
    assert WineUtils.unlink_user_profile_links(str(prefix)) is False
    assert desktop.is_symlink()


def test_does_not_delete_entry_replacing_scanned_link(monkeypatch, tmp_path):
    prefix = tmp_path / "prefix"
    profile = prefix / "drive_c" / "users" / "otheruser"
    profile.mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    desktop = profile / "Desktop"
    desktop.symlink_to(target)
    rename = wine_module.os.rename
    replaced = False

    def replace_before_rename(src, dst, *args, **kwargs):
        nonlocal replaced
        if src == "Desktop" and not replaced:
            desktop.unlink()
            desktop.write_text("keep me")
            replaced = True
        return rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(wine_module.os, "rename", replace_before_rename)

    assert WineUtils.unlink_user_profile_links(str(prefix)) is False
    assert desktop.is_file()
    assert desktop.read_text() == "keep me"


def test_continues_after_one_link_cannot_be_replaced(monkeypatch, tmp_path):
    prefix = tmp_path / "prefix"
    profile = prefix / "drive_c" / "users" / "otheruser"
    profile.mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    desktop = profile / "Desktop"
    documents = profile / "Documents"
    desktop.symlink_to(target)
    documents.symlink_to(target)
    mkdir = wine_module.os.mkdir

    def fail_desktop(path, *args, **kwargs):
        if path == "Desktop":
            raise OSError
        return mkdir(path, *args, **kwargs)

    monkeypatch.setattr(wine_module.os, "mkdir", fail_desktop)

    assert WineUtils.unlink_user_profile_links(str(prefix)) is False
    assert desktop.is_symlink()
    assert documents.is_dir()
    assert not documents.is_symlink()


def test_rolls_back_when_backup_link_cannot_be_removed(monkeypatch, tmp_path):
    prefix = tmp_path / "prefix"
    profile = prefix / "drive_c" / "users" / "otheruser"
    profile.mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    desktop = profile / "Desktop"
    desktop.symlink_to(target)
    unlink = wine_module.os.unlink

    def fail_backup(path, *args, **kwargs):
        if str(path).startswith(".runexe-link-"):
            raise OSError
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(wine_module.os, "unlink", fail_backup)

    assert WineUtils.unlink_user_profile_links(str(prefix)) is False
    assert desktop.is_symlink()
    assert not list(profile.glob(".runexe-link-*"))


def test_discards_backup_link_when_replacement_directory_is_in_use(
    monkeypatch, tmp_path
):
    prefix = tmp_path / "prefix"
    profile = prefix / "drive_c" / "users" / "otheruser"
    profile.mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    desktop = profile / "Desktop"
    desktop.symlink_to(target)
    unlink = wine_module.os.unlink
    failed_once = False

    def fail_first_backup(path, *args, **kwargs):
        nonlocal failed_once
        if str(path).startswith(".runexe-link-") and not failed_once:
            failed_once = True
            (desktop / "concurrent-file").write_text("keep me")
            raise OSError
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(wine_module.os, "unlink", fail_first_backup)

    assert WineUtils.unlink_user_profile_links(str(prefix)) is False
    assert (desktop / "concurrent-file").read_text() == "keep me"
    assert not list(profile.glob(".runexe-link-*"))


def test_reports_failure_when_moved_link_disappears(monkeypatch, tmp_path):
    prefix = tmp_path / "prefix"
    profile = prefix / "drive_c" / "users" / "otheruser"
    profile.mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    desktop = profile / "Desktop"
    desktop.symlink_to(target)
    mkdir = wine_module.os.mkdir

    def remove_backup_and_fail(path, *args, **kwargs):
        if path == "Desktop":
            backup = next(profile.glob(".runexe-link-*"))
            backup.unlink()
            raise OSError
        return mkdir(path, *args, **kwargs)

    monkeypatch.setattr(wine_module.os, "mkdir", remove_backup_and_fail)

    assert WineUtils.unlink_user_profile_links(str(prefix)) is False
    assert not desktop.exists()


def test_direct_link_failure_does_not_skip_nested_links(monkeypatch, tmp_path):
    prefix = tmp_path / "prefix"
    profile = prefix / "drive_c" / "users" / "otheruser"
    documents = profile / "Documents"
    documents.mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    desktop = profile / "Desktop"
    music = documents / "My Music"
    desktop.symlink_to(target)
    music.symlink_to(target)
    mkdir = wine_module.os.mkdir

    def fail_desktop(path, *args, **kwargs):
        if path == "Desktop":
            raise OSError
        return mkdir(path, *args, **kwargs)

    monkeypatch.setattr(wine_module.os, "mkdir", fail_desktop)

    assert WineUtils.unlink_user_profile_links(str(prefix)) is False
    assert desktop.is_symlink()
    assert music.is_dir()
    assert not music.is_symlink()


def test_rollback_does_not_overwrite_concurrent_entry(tmp_path):
    directory = tmp_path / "directory"
    directory.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    backup = directory / ".runexe-link-backup"
    backup.symlink_to(target)
    desktop = directory / "Desktop"
    desktop.write_text("concurrent data")
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)

    try:
        restored = WineUtils._restore_moved_entry(
            directory_fd,
            backup.name,
            desktop.name,
            discard_symlink_on_conflict=True,
        )
    finally:
        os.close(directory_fd)

    assert restored is False
    assert desktop.read_text() == "concurrent data"
    assert not backup.exists()


