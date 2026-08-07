from __future__ import annotations

import ctypes
import re
import subprocess
import sys
from ctypes import wintypes


SERVICE_NAME = "RelaxCreatorStudio"
_PROVIDER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168


class SecretStoreError(RuntimeError):
    """Raised when the operating-system credential store cannot be used."""


def _provider_id(provider_id: str) -> str:
    value = provider_id.strip()
    if not _PROVIDER_PATTERN.fullmatch(value):
        raise SecretStoreError("模型服务商标识无效，无法保存 API Key。")
    return value


def _windows_target(provider_id: str) -> str:
    return f"{SERVICE_NAME}/api-key/{provider_id}"


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _windows_api():
    try:
        api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    except (AttributeError, OSError) as exc:
        raise SecretStoreError("无法打开 Windows 凭据管理器。") from exc

    api.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_CREDENTIALW)),
    ]
    api.CredReadW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api


def _windows_read(provider_id: str) -> str:
    api = _windows_api()
    credential_ptr = ctypes.POINTER(_CREDENTIALW)()
    if not api.CredReadW(
        _windows_target(provider_id),
        _CRED_TYPE_GENERIC,
        0,
        ctypes.byref(credential_ptr),
    ):
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            return ""
        raise SecretStoreError(f"读取 Windows 凭据失败（错误 {error}）。")

    try:
        credential = credential_ptr.contents
        if not credential.CredentialBlob or not credential.CredentialBlobSize:
            return ""
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return raw.decode("utf-16-le")
    except (UnicodeDecodeError, ValueError) as exc:
        raise SecretStoreError("Windows 凭据中的 API Key 无法读取。") from exc
    finally:
        api.CredFree(credential_ptr)


def _windows_write(provider_id: str, api_key: str) -> None:
    api = _windows_api()
    encoded = api_key.encode("utf-16-le")
    blob = ctypes.create_string_buffer(encoded)
    credential = _CREDENTIALW()
    credential.Type = _CRED_TYPE_GENERIC
    credential.TargetName = _windows_target(provider_id)
    credential.CredentialBlobSize = len(encoded)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = provider_id
    if not api.CredWriteW(ctypes.byref(credential), 0):
        error = ctypes.get_last_error()
        raise SecretStoreError(f"写入 Windows 凭据失败（错误 {error}）。")


def _windows_delete(provider_id: str) -> None:
    api = _windows_api()
    if api.CredDeleteW(_windows_target(provider_id), _CRED_TYPE_GENERIC, 0):
        return
    error = ctypes.get_last_error()
    if error != _ERROR_NOT_FOUND:
        raise SecretStoreError(f"删除 Windows 凭据失败（错误 {error}）。")


def _mac_not_found(message: str) -> bool:
    lowered = message.lower()
    return "could not be found" in lowered or "not found" in lowered or "seckeychainsearchcopynext" in lowered


def _run_security(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["/usr/bin/security", *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError as exc:
        raise SecretStoreError("无法打开 macOS 钥匙串。") from exc


def _mac_read(provider_id: str) -> str:
    result = _run_security(["find-generic-password", "-s", SERVICE_NAME, "-a", provider_id, "-w"])
    if result.returncode == 0:
        return result.stdout.rstrip("\r\n")
    if _mac_not_found(result.stderr):
        return ""
    raise SecretStoreError("读取 macOS 钥匙串中的 API Key 失败。")


def _mac_write(provider_id: str, api_key: str) -> None:
    result = _run_security(
        ["add-generic-password", "-U", "-s", SERVICE_NAME, "-a", provider_id, "-w", api_key]
    )
    if result.returncode != 0:
        raise SecretStoreError("写入 macOS 钥匙串失败。")


def _mac_delete(provider_id: str) -> None:
    result = _run_security(["delete-generic-password", "-s", SERVICE_NAME, "-a", provider_id])
    if result.returncode != 0 and not _mac_not_found(result.stderr):
        raise SecretStoreError("删除 macOS 钥匙串中的 API Key 失败。")


def load_api_key(provider_id: str) -> str:
    provider_id = _provider_id(provider_id)
    if sys.platform == "win32":
        return _windows_read(provider_id)
    if sys.platform == "darwin":
        return _mac_read(provider_id)
    return ""


def save_api_key(provider_id: str, api_key: str) -> None:
    provider_id = _provider_id(provider_id)
    value = api_key.strip()
    if not value:
        delete_api_key(provider_id)
        return
    if sys.platform == "win32":
        _windows_write(provider_id, value)
    elif sys.platform == "darwin":
        _mac_write(provider_id, value)
    else:
        raise SecretStoreError("当前系统不支持安全保存 API Key。")


def delete_api_key(provider_id: str) -> None:
    provider_id = _provider_id(provider_id)
    if sys.platform == "win32":
        _windows_delete(provider_id)
    elif sys.platform == "darwin":
        _mac_delete(provider_id)
