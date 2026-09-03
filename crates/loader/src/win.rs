//! The handful of Win32 functions the loader needs, declared by hand: the
//! DLL has no dependency to update and links against nothing but Windows.
#![allow(non_snake_case, clippy::upper_case_acronyms)]

use core::ffi::c_void;

pub type HMODULE = *mut c_void;
pub type HANDLE = *mut c_void;
pub type BOOL = i32;

pub const PAGE_EXECUTE_READWRITE: u32 = 0x40;
pub const ENUM_CURRENT_SETTINGS: u32 = 0xFFFF_FFFF;
/// sizeof(DEVMODEW); the loader reads the two fields it needs by offset.
pub const DEVMODE_SIZE: usize = 220;
pub const DEVMODE_SIZE_AT: usize = 68;
pub const DEVMODE_WIDTH_AT: usize = 172;
pub const DEVMODE_HEIGHT_AT: usize = 176;

#[link(name = "kernel32", kind = "raw-dylib")]
unsafe extern "system" {
    pub fn GetModuleHandleW(name: *const u16) -> HMODULE;
    pub fn GetModuleFileNameW(module: HMODULE, buf: *mut u16, len: u32) -> u32;
    pub fn LoadLibraryW(name: *const u16) -> HMODULE;
    pub fn GetProcAddress(module: HMODULE, name: *const u8) -> *const c_void;
    pub fn GetSystemDirectoryW(buf: *mut u16, len: u32) -> u32;
    pub fn VirtualProtect(addr: *const c_void, size: usize, new: u32, old: *mut u32) -> BOOL;
    pub fn FlushInstructionCache(process: HANDLE, addr: *const c_void, size: usize) -> BOOL;
    pub fn GetCurrentProcess() -> HANDLE;
    pub fn DisableThreadLibraryCalls(module: HMODULE) -> BOOL;
    pub fn OutputDebugStringW(text: *const u16);
    pub fn GetLastError() -> u32;
}

#[link(name = "user32", kind = "raw-dylib")]
unsafe extern "system" {
    pub fn EnumDisplaySettingsW(device: *const u16, mode: u32, devmode: *mut u8) -> BOOL;
}

pub fn wide(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(Some(0)).collect()
}
