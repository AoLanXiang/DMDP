#include <windows.h>
#include <wchar.h>
static int fail(const wchar_t *m){ MessageBoxW(NULL,m,L"DMDP",MB_ICONERROR|MB_OK); return 1; }
int WINAPI wWinMain(HINSTANCE h, HINSTANCE p, LPWSTR lpCmdLine, int nShow){
    wchar_t exePath[MAX_PATH];
    if(!GetModuleFileNameW(NULL,exePath,MAX_PATH)) return fail(L"无法获取程序路径。");
    wchar_t *slash = wcsrchr(exePath, L'\\'); if(slash) *slash = L'\0';
    wchar_t prog[MAX_PATH]; wcscpy(prog, exePath);
    wchar_t home[MAX_PATH]; wcscpy(home, exePath);
    wchar_t *base = wcsrchr(home, L'\\');
    if(base && _wcsicmp(base+1, L"DMDP")==0){
        // 只有"上级目录里还有 DMDP.exe"时才说明这是单文件版的依赖目录
        wchar_t parent[MAX_PATH]; wcscpy(parent, home);
        parent[base-parent]=L'\0';
        wchar_t marker[MAX_PATH];
        _snwprintf(marker, MAX_PATH, L"%s\\DMDP.exe", parent);
        if(GetFileAttributesW(marker)!=INVALID_FILE_ATTRIBUTES) wcscpy(home, parent);
    }
    wchar_t env[MAX_PATH];
    if(GetEnvironmentVariableW(L"DMDP_HOME", env, MAX_PATH) && GetFileAttributesW(env)!=INVALID_FILE_ATTRIBUTES) wcscpy(home, env);
    wchar_t py[MAX_PATH*2], app[MAX_PATH*2], cmd[MAX_PATH*6];
    _snwprintf(py, MAX_PATH*2, L"%s\\runtime\\pythonw.exe", prog);
    _snwprintf(app, MAX_PATH*2, L"%s\\app\\dmdp.py", prog);
    if(GetFileAttributesW(py)==INVALID_FILE_ATTRIBUTES) return fail(L"找不到 runtime\\pythonw.exe。");
    if(GetFileAttributesW(app)==INVALID_FILE_ATTRIBUTES) return fail(L"找不到 app\\dmdp.py。");
    SetEnvironmentVariableW(L"DMDP_HOME", home);
    _snwprintf(cmd, MAX_PATH*6, L"\"%s\" \"%s\" %s", py, app, lpCmdLine?lpCmdLine:L"");
    STARTUPINFOW si; PROCESS_INFORMATION pi;
    ZeroMemory(&si,sizeof(si)); si.cb=sizeof(si); ZeroMemory(&pi,sizeof(pi));
    if(!CreateProcessW(NULL,cmd,NULL,NULL,FALSE,0,NULL,home,&si,&pi)) return fail(L"启动 Python 运行时失败。");
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess); return 0;
}
