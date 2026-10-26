"""Dedicated Win32 edit-control fixture for opt-in native input testing."""
import ctypes
from ctypes import wintypes
import sys

user = ctypes.WinDLL("user32", use_last_error=True)
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel.GetModuleHandleW.restype = wintypes.HINSTANCE
callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user.DefWindowProcW.restype = ctypes.c_ssize_t
@callback_type
def procedure(window, message, wparam, lparam):
    return user.DefWindowProcW(window, message, wparam, lparam)
class WindowClass(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("procedure", callback_type),
        ("class_extra", ctypes.c_int), ("window_extra", ctypes.c_int),
        ("instance", wintypes.HINSTANCE), ("icon", wintypes.HICON), ("cursor", wintypes.HANDLE),
        ("background", wintypes.HBRUSH), ("menu", wintypes.LPCWSTR), ("name", wintypes.LPCWSTR)]
instance = kernel.GetModuleHandleW(None)
definition = WindowClass(0, procedure, 0, 0, instance, None, None, 6, None, "SentinelNativeInputFixture")
user.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
user.RegisterClassW.restype = wintypes.ATOM
if not user.RegisterClassW(ctypes.byref(definition)):
    raise ctypes.WinError(ctypes.get_last_error())
user.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user.CreateWindowExW.restype = wintypes.HWND
user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user.SetForegroundWindow.argtypes = [wintypes.HWND]
user.UpdateWindow.argtypes = [wintypes.HWND]
window = user.CreateWindowExW(0, definition.name, sys.argv[1], 0x00CF0000,
    160, 160, 620, 320, None, None, instance, None)
if not window:
    raise ctypes.WinError(ctypes.get_last_error())
user.CreateWindowExW(0, "STATIC", "Test input", 0x50000000,
    25, 30, 500, 25, window, None, None, None)
user.CreateWindowExW(0x200, "EDIT", "", 0x50810004,
    25, 65, 530, 120, window, None, None, None)
user.ShowWindow(window, 5)
user.UpdateWindow(window)
user.SetForegroundWindow(window)
print(int(window), flush=True)
message = wintypes.MSG()
while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
    user.TranslateMessage(ctypes.byref(message))
    user.DispatchMessageW(ctypes.byref(message))
