#include <windows.h>
#include <wchar.h>

static int fail(const wchar_t *msg) {
    MessageBoxW(NULL, msg, L"DMDP", MB_ICONERROR | MB_OK);
    return 1;
}

int WINAPI wWinMain(HINSTANCE h, HINSTANCE p, LPWSTR lpCmdLine, int nShow) {
    wchar_t exePath[MAX_PATH];
    if (!GetModuleFileNameW(NULL, exePath, MAX_PATH)) return fail(L"无法获取程序路径。");
    wchar_t *slash = wcsrchr(exePath, L'\\');
    if (slash) *slash = L'\0';

    // 程序文件目录：永远是 exe 自己所在目录（runtime、app 都在这里）
    wchar_t prog[MAX_PATH];
    wcscpy(prog, exePath);

    // 游戏目录：默认 exe 所在目录；若 exe 在 DMDP 子目录里，游戏目录取它的上级
    wchar_t home[MAX_PATH];
    wcscpy(home, exePath);
    wchar_t *base = wcsrchr(home, L'\\');
    if (base && _wcsicmp(base + 1, L"DMDP") == 0) { home[base - home] = L'\0'; }

    // 环境变量可覆盖（排错或特殊部署用）
    wchar_t env[MAX_PATH];
    if (GetEnvironmentVariableW(L"DMDP_HOME", env, MAX_PATH) &&
        GetFileAttributesW(env) != INVALID_FILE_ATTRIBUTES) {
        wcscpy(home, env);
    }

    wchar_t py[MAX_PATH * 2], app[MAX_PATH * 2], cmd[MAX_PATH * 6];
    _snwprintf(py, MAX_PATH * 2, L"%s\\runtime\\pythonw.exe", prog);
    _snwprintf(app, MAX_PATH * 2, L"%s\\app\\dmdp.py", prog);

    if (GetFileAttributesW(py) == INVALID_FILE_ATTRIBUTES)
        return fail(L"找不到 runtime\\pythonw.exe。\n请把 DMDP.exe 与 runtime、app 放在同一目录。");
    if (GetFileAttributesW(app) == INVALID_FILE_ATTRIBUTES)
        return fail(L"找不到 app\\dmdp.py。\n程序包不完整。");

    SetEnvironmentVariableW(L"DMDP_HOME", home);
    _snwprintf(cmd, MAX_PATH * 6, L"\"%s\" \"%s\" %s", py, app, lpCmdLine ? lpCmdLine : L"");

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    if (!CreateProcessW(NULL, cmd, NULL, NULL, FALSE, 0, NULL, home, &si, &pi))
        return fail(L"启动 Python 运行时失败。\n请用「带控制台启动（排错用）.bat」查看具体报错。");

    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
