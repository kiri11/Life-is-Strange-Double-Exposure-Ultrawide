//! The real winhttp.dll's functions, forwarded to the copy in System32.
//!
//! Why winhttp: the proxy has to be a DLL the game imports and that nothing
//! loads from System32 *before* the game's imports are resolved, because the
//! loader reuses a module already in memory by name. Windows' compatibility
//! shim engine (`AcGenral.dll`, active whenever a player has set any
//! compatibility option on the executable, high-DPI override included) is
//! loaded first of all and imports `version.dll`, `userenv.dll`, `mpr.dll`
//! and a few others, so those names can never be proxied reliably. No shim
//! DLL touches winhttp.
//!
//! Every argument of these functions is an integer or a pointer, which the
//! x64 calling convention passes the same way whatever its declared type:
//! the first four in registers, the rest on the stack above the return
//! address. Each forwarder therefore takes sixteen machine words, more than
//! any of them uses, and hands them all on. The surplus are reads of the
//! caller's own frame, which is always mapped, and the callee ignores them.
//! The return value travels back in rax likewise.
//!
//! The forwarders come in blocks, one per System32 library this file can
//! stand in for. Windows binds only the names the game imports from the file
//! it found, so a second proxy name, should another mod ever hold winhttp.dll,
//! is one more `forward!` block here and one more entry in the game descriptor's `proxy_dlls()`,
//! nothing else in the crate - once that name has passed the load-order
//! check that ruled version.dll out.
#![allow(non_snake_case)]

use core::ffi::c_void;
use core::sync::atomic::{AtomicUsize, Ordering};

use crate::win;

/// A library in System32, loaded the first time one of its functions is
/// called: never from `DllMain`.
struct SystemDll {
    name: &'static str,
    handle: AtomicUsize,
}

impl SystemDll {
    const fn new(name: &'static str) -> Self {
        SystemDll { name, handle: AtomicUsize::new(0) }
    }

    fn handle(&self) -> usize {
        let h = self.handle.load(Ordering::Acquire);
        if h != 0 {
            return h;
        }
        let mut buf = [0u16; 260];
        let n = unsafe { win::GetSystemDirectoryW(buf.as_mut_ptr(), buf.len() as u32) } as usize;
        if n == 0 || n >= buf.len() {
            return 0;
        }
        let mut path = buf[..n].to_vec();
        path.push(u16::from(b'\\'));
        path.extend(self.name.encode_utf16());
        path.push(0);
        let h = unsafe { win::LoadLibraryW(path.as_ptr()) } as usize;
        self.handle.store(h, Ordering::Release);
        h
    }

    fn resolve(&self, cache: &AtomicUsize, name: &'static str) -> usize {
        let f = cache.load(Ordering::Acquire);
        if f != 0 {
            return f;
        }
        let module = self.handle();
        if module == 0 {
            return 0;
        }
        let f = unsafe { win::GetProcAddress(module as *mut c_void, name.as_ptr()) } as usize;
        cache.store(f, Ordering::Release);
        f
    }
}

static WINHTTP: SystemDll = SystemDll::new("winhttp.dll");

type Forwarded = unsafe extern "system" fn(
    usize, usize, usize, usize, usize, usize, usize, usize,
    usize, usize, usize, usize, usize, usize, usize, usize,
) -> usize;

macro_rules! forward {
    ($dll:ident: $($name:ident)*) => {$(
        #[unsafe(no_mangle)]
        pub unsafe extern "system" fn $name(
            a: usize, b: usize, c: usize, d: usize, e: usize, f: usize, g: usize, h: usize,
            i: usize, j: usize, k: usize, l: usize, m: usize, n: usize, o: usize, p: usize,
        ) -> usize {
            static CACHE: AtomicUsize = AtomicUsize::new(0);
            let target = $dll.resolve(&CACHE, concat!(stringify!($name), "\0"));
            if target == 0 {
                return 0;
            }
            let target: Forwarded = unsafe { core::mem::transmute(target) };
            unsafe { target(a, b, c, d, e, f, g, h, i, j, k, l, m, n, o, p) }
        }
    )*};
}

// Every named export of Windows 11's winhttp.dll (it has no ordinal-only ones).
forward! { WINHTTP:
    DllCanUnloadNow DllGetClassObject Private1 SvchostPushServiceGlobals
    WinHttpAddRequestHeaders WinHttpAddRequestHeadersEx WinHttpAutoProxySvcMain
    WinHttpCheckPlatform WinHttpCloseHandle WinHttpConnect
    WinHttpConnectionDeletePolicyEntries WinHttpConnectionDeleteProxyInfo
    WinHttpConnectionFreeNameList WinHttpConnectionFreeProxyInfo
    WinHttpConnectionFreeProxyList WinHttpConnectionGetNameList
    WinHttpConnectionGetProxyInfo WinHttpConnectionGetProxyList
    WinHttpConnectionOnlyConvert WinHttpConnectionOnlyReceive WinHttpConnectionOnlySend
    WinHttpConnectionSetPolicyEntries WinHttpConnectionSetProxyInfo
    WinHttpConnectionUpdateIfIndexTable WinHttpCrackUrl WinHttpCreateProxyList
    WinHttpCreateProxyManager WinHttpCreateProxyResolver WinHttpCreateProxyResult
    WinHttpCreateUiCompatibleProxyString WinHttpCreateUrl WinHttpDetectAutoProxyConfigUrl
    WinHttpFreeProxyResult WinHttpFreeProxyResultEx WinHttpFreeProxySettings
    WinHttpFreeProxySettingsEx WinHttpFreeQueryConnectionGroupResult
    WinHttpGetDefaultProxyConfiguration WinHttpGetIEProxyConfigForCurrentUser
    WinHttpGetProxyForUrl WinHttpGetProxyForUrlEx WinHttpGetProxyForUrlEx2
    WinHttpGetProxyForUrlHvsi WinHttpGetProxyResult WinHttpGetProxyResultEx
    WinHttpGetProxySettingsEx WinHttpGetProxySettingsResultEx WinHttpGetProxySettingsVersion
    WinHttpGetTunnelSocket WinHttpOpen WinHttpOpenRequest WinHttpPacJsWorkerMain
    WinHttpProbeConnectivity WinHttpProtocolCompleteUpgrade WinHttpProtocolReceive
    WinHttpProtocolSend WinHttpQueryAuthSchemes WinHttpQueryConnectionGroup
    WinHttpQueryDataAvailable WinHttpQueryHeaders WinHttpQueryHeadersEx WinHttpQueryOption
    WinHttpReadData WinHttpReadDataEx WinHttpReadProxySettings WinHttpReadProxySettingsHvsi
    WinHttpReceiveResponse WinHttpRefreshProxySettings WinHttpRegisterProxyChangeNotification
    WinHttpResetAutoProxy WinHttpResolverGetProxyForUrl WinHttpSaveProxyCredentials
    WinHttpSendRequest WinHttpSetCredentials WinHttpSetDefaultProxyConfiguration
    WinHttpSetOption WinHttpSetProxySettingsPerUser WinHttpSetSecureLegacyServersAppCompat
    WinHttpSetStatusCallback WinHttpSetTimeouts WinHttpTimeFromSystemTime
    WinHttpTimeToSystemTime WinHttpUnregisterProxyChangeNotification WinHttpWebSocketClose
    WinHttpWebSocketCompleteUpgrade WinHttpWebSocketQueryCloseStatus WinHttpWebSocketReceive
    WinHttpWebSocketSend WinHttpWebSocketShutdown WinHttpWriteData WinHttpWriteProxySettings
}
