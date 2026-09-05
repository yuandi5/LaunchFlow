# LaunchFlow · 一键启动器

一个轻量的 Windows 桌面工具，将常用软件组合成启动方案，一键按顺序打开，适合办公、开发和游戏等使用场景。

## 功能

- **多方案管理**：创建、重命名和删除启动方案，为不同场景保存应用组合。
- **快速添加应用**：搜索本机程序、浏览选择文件，或直接拖入 `.exe` / `.lnk`，支持拖拽调整启动顺序。
- **控制启动流程**：设置启动参数、逐项延迟，以及进程出现、窗口出现或端口可连接等等待条件和超时时间。
- **灵活启动设置**：支持管理员权限、正常 / 最小化 / 最大化窗口，直接添加的 `.exe` 可在已运行时跳过。
- **便捷入口**：系统托盘运行、开机自启动，并可为指定方案创建桌面快捷方式。
- **配置与日志**：支持 JSON 配置导入导出，实时显示启动日志并保存到本地。

## 使用

打开工具后，新建方案 → 添加应用 → 拖拽排序并按需编辑启动设置 → 点击「一键启动当前方案」。

### 源码运行

需要 Windows 和 Python 3.10+（包含 Tkinter）。

```powershell
python -m pip install -r requirements.txt
python launcher.py
```

### 打包为 EXE

```powershell
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean OneClickLauncher.spec
```

生成文件位于 `dist/OneClickLauncher.exe`，运行时无需安装 Python。开机自启动和方案桌面快捷方式建议使用 EXE 版本。

配置与日志保存在 `%APPDATA%\OneClickLauncher\`。
