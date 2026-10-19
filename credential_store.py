"""Windows Credential Manager integration for saved Aules credentials."""

import ctypes
import hashlib
import sys
from ctypes import wintypes
from typing import Optional, Tuple


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168


class _FileTime(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FileTime),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _windows_api():
    if sys.platform != "win32":
        raise OSError("El almacén seguro de credenciales solo está disponible en Windows.")

    advapi32 = ctypes.WinDLL("Advapi32", use_last_error=True)
    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_Credential)),
    ]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    advapi32.CredFree.restype = None
    return advapi32


def _target_name(base_url: str) -> str:
    normalized_url = base_url.strip().rstrip("/").casefold()
    digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()
    return f"AulesPDFSynchronizer:{digest}"


def load_credentials(base_url: str) -> Optional[Tuple[str, str]]:
    """Return (username, password) from the current Windows user's vault."""
    api = _windows_api()
    credential = ctypes.POINTER(_Credential)()
    if not api.CredReadW(_target_name(base_url), CRED_TYPE_GENERIC, 0, ctypes.byref(credential)):
        error = ctypes.get_last_error()
        if error == ERROR_NOT_FOUND:
            return None
        raise ctypes.WinError(error)

    try:
        data = ctypes.string_at(credential.contents.CredentialBlob, credential.contents.CredentialBlobSize)
        password = data.decode("utf-16-le")
        return credential.contents.UserName or "", password
    finally:
        api.CredFree(credential)


def save_credentials(base_url: str, username: str, password: str) -> None:
    """Save credentials encrypted by Windows for the current user's logon."""
    api = _windows_api()
    blob = ctypes.create_string_buffer(password.encode("utf-16-le"))
    credential = _Credential()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = _target_name(base_url)
    credential.CredentialBlobSize = len(password.encode("utf-16-le"))
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = username
    if not api.CredWriteW(ctypes.byref(credential), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def delete_credentials(base_url: str) -> None:
    """Remove saved credentials for this Aules base URL."""
    api = _windows_api()
    if not api.CredDeleteW(_target_name(base_url), CRED_TYPE_GENERIC, 0):
        error = ctypes.get_last_error()
        if error != ERROR_NOT_FOUND:
            raise ctypes.WinError(error)
