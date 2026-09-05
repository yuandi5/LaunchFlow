import ctypes
import json
import os
import queue
import shlex
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES, TkinterDnD = None, None

APP_NAME = "一键启动器"
BASE_DIR = Path(os.environ.get("APPDATA", Path.home())) / "OneClickLauncher"
CONFIG_FILE = BASE_DIR / "config.json"
LOG_FILE = BASE_DIR / "launcher.log"


def resource_path(name):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / name


@dataclass
class AppItem:
    name: str
    path: str
    args: str = ""
    delay: float = 0
    wait_type: str = "none"  # none/process/window/port
    wait_value: str = ""
    wait_timeout: int = 60
    run_as_admin: bool = False
    skip_if_running: bool = True
    window_state: str = "normal"  # normal/minimized/maximized


@dataclass
class Profile:
    name: str
    items: list = field(default_factory=list)


def load_data():
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            profiles = [Profile(p["name"], [AppItem(**i) for i in p.get("items", [])]) for p in raw.get("profiles", [])]
            if profiles:
                return profiles, bool(raw.get("autostart", False))
        except Exception:
            pass
    return [Profile("默认方案")], False


def save_data(profiles, autostart):
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps({"profiles": [asdict(p) for p in profiles], "autostart": autostart}, ensure_ascii=False, indent=2), encoding="utf-8")


def now():
    return datetime.now().strftime("%H:%M:%S")


class LauncherApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        try:
            self.root.iconbitmap(str(resource_path("app.ico")))
        except Exception:
            pass
        self.root.geometry("980x620")
        self.root.minsize(820, 500)
        self.profiles, self.autostart = load_data()
        self.running = False
        self.stop_event = threading.Event()
        self.started_pids = []
        self.log_queue = queue.Queue()
        self.tray = None
        self._build_ui()
        self._refresh_profiles()
        self.root.after(100, self._drain_logs)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.root.bind("<Unmap>", self._on_minimize)
        self._setup_tray()

    def log(self, text):
        line = f"[{now()}] {text}"
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        self.log_queue.put(line)

    def _drain_logs(self):
        while not self.log_queue.empty():
            self.log_text.insert("end", self.log_queue.get() + "\n")
            self.log_text.see("end")
        self.root.after(100, self._drain_logs)

    def _build_ui(self):
        self.root.configure(bg="#f4f6f8")
        style = ttk.Style(self.root); style.theme_use("clam")
        style.configure("TLabel", background="#f4f6f8", foreground="#263445", font=("Microsoft YaHei UI", 10))
        style.configure("Primary.TButton", background="#2563eb", foreground="white", padding=(16, 10), font=("Microsoft YaHei UI", 11, "bold"))
        style.map("Primary.TButton", background=[("active", "#1d4ed8")])
        style.configure("Treeview", rowheight=30, font=("Microsoft YaHei UI", 9), fieldbackground="white")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        top = ttk.Frame(self.root, padding=12); top.pack(fill="x")
        ttk.Button(top, text="新建方案", command=self.new_profile).pack(side="left")
        ttk.Button(top, text="重命名", command=self.rename_profile).pack(side="left", padx=4)
        ttk.Button(top, text="删除方案", command=self.delete_profile).pack(side="left")
        ttk.Button(top, text="创建桌面快捷方式", command=self.create_profile_shortcut).pack(side="left", padx=(14, 4))
        ttk.Button(top, text="导出配置", command=self.export_config).pack(side="right")
        ttk.Button(top, text="导入配置", command=self.import_config).pack(side="right", padx=4)
        self.autostart_var = tk.BooleanVar(value=self.autostart)
        ttk.Checkbutton(top, text="开机自启动", variable=self.autostart_var, command=self.toggle_autostart).pack(side="right", padx=12)
        self.start_button = ttk.Button(top, text="▶  一键启动当前方案", style="Primary.TButton", command=self.start_profile)
        self.start_button.pack(side="right", padx=8, ipadx=10, ipady=4)
        body = ttk.Panedwindow(self.root, orient="horizontal"); body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        left = ttk.Frame(body, padding=(0, 0, 10, 0)); body.add(left, weight=1)
        right = ttk.Frame(body); body.add(right, weight=4)
        ttk.Label(left, text="启动方案").pack(anchor="w")
        self.profile_list = tk.Listbox(left, exportselection=False, activestyle="dotbox", font=("Microsoft YaHei UI", 11), bg="white", fg="#263445", selectbackground="#dbeafe", selectforeground="#1d4ed8", relief="flat", highlightthickness=1, highlightcolor="#93c5fd")
        self.profile_list.pack(fill="both", expand=True, pady=(4, 0)); self.profile_list.bind("<<ListboxSelect>>", lambda e: self._refresh_items())
        bar = ttk.Frame(right); bar.pack(fill="x")
        ttk.Label(bar, text="方案应用").pack(side="left")
        ttk.Button(bar, text="添加文件", command=self.add_item).pack(side="right")
        ttk.Button(bar, text="编辑", command=self.edit_item).pack(side="right", padx=4)
        ttk.Button(bar, text="删除", command=self.remove_item).pack(side="right")
        self.item_tree = ttk.Treeview(right, columns=("name", "path", "wait", "delay", "status"), show="headings", height=14)
        for col, title, width in [("name", "名称", 150), ("path", "路径", 360), ("wait", "等待条件", 130), ("delay", "延迟", 70), ("status", "状态", 90)]:
            self.item_tree.heading(col, text=title); self.item_tree.column(col, width=width, anchor="w")
        self.item_tree.pack(fill="both", expand=True, pady=(4, 8)); self.item_tree.bind("<Double-1>", lambda e: self.edit_item())
        self.item_tree.bind("<ButtonPress-1>", self._begin_item_drag)
        self.item_tree.bind("<B1-Motion>", self._drag_item)
        self.item_tree.bind("<ButtonRelease-1>", self._finish_item_drag)
        if DND_FILES:
            self.item_tree.drop_target_register(DND_FILES); self.item_tree.dnd_bind("<<Drop>>", self._drop_files)
        ttk.Label(right, text="支持将 .lnk 或 .exe 直接拖入上方列表").pack(anchor="w")
        ttk.Label(right, text="启动日志").pack(anchor="w", pady=(8, 0))
        self.log_text = tk.Text(right, height=8, bg="#1f2937", fg="#e5e7eb", insertbackground="white", font=("Consolas", 9), relief="flat")
        self.log_text.pack(fill="both", expand=True, pady=(4, 0))

    def _drop_files(self, event):
        p = self._current_profile()
        if not p: return
        try: paths = self.root.tk.splitlist(event.data)
        except Exception: paths = [event.data]
        added = 0
        for path in paths:
            path = path.strip('{}')
            if Path(path).suffix.lower() in ('.lnk', '.exe') and Path(path).exists():
                p.items.append(AppItem(Path(path).stem, path, delay=len(p.items))); added += 1
        if added:
            save_data(self.profiles, self.autostart_var.get()); self._refresh_items(); self.log(f"已拖入 {added} 个应用")

    def _current_profile(self):
        sel = self.profile_list.curselection(); return self.profiles[sel[0]] if sel else None

    def _refresh_profiles(self):
        self.profile_list.delete(0, "end")
        for p in self.profiles: self.profile_list.insert("end", p.name)
        if self.profiles: self.profile_list.selection_set(0)
        self._refresh_items()

    def _refresh_items(self):
        self.item_tree.delete(*self.item_tree.get_children())
        p = self._current_profile()
        if not p: return
        for i in p.items:
            wait = "无" if i.wait_type == "none" else f"{i.wait_type}: {i.wait_value}"
            self.item_tree.insert("", "end", values=(i.name, i.path, wait, f"{i.delay:g}s", "就绪"))

    def _begin_item_drag(self, event):
        row = self.item_tree.identify_row(event.y)
        self._drag_item_index = self.item_tree.index(row) if row else None

    def _drag_item(self, event):
        if getattr(self, "_drag_item_index", None) is None: return
        row = self.item_tree.identify_row(event.y)
        if not row: return
        target = self.item_tree.index(row)
        if target == self._drag_item_index: return
        p = self._current_profile()
        if not p: return
        item = p.items.pop(self._drag_item_index); p.items.insert(target, item)
        self._drag_item_index = target
        self._refresh_items()
        children = self.item_tree.get_children()
        if children: self.item_tree.selection_set(children[target])

    def _finish_item_drag(self, _event):
        if getattr(self, "_drag_item_index", None) is not None:
            save_data(self.profiles, self.autostart_var.get())
        self._drag_item_index = None

    def new_profile(self):
        name = simpledialog.askstring("新建方案", "方案名称：", parent=self.root)
        if name and name.strip(): self.profiles.append(Profile(name.strip())); save_data(self.profiles, self.autostart_var.get()); self._refresh_profiles()

    def rename_profile(self):
        p = self._current_profile()
        if not p: return
        name = simpledialog.askstring("重命名", "方案名称：", initialvalue=p.name, parent=self.root)
        if name and name.strip(): p.name = name.strip(); save_data(self.profiles, self.autostart_var.get()); self._refresh_profiles()

    def delete_profile(self):
        if len(self.profiles) == 1: return messagebox.showinfo("提示", "至少保留一个方案。")
        p = self._current_profile()
        if p and messagebox.askyesno("确认", f"删除方案“{p.name}”？"): self.profiles.remove(p); save_data(self.profiles, self.autostart_var.get()); self._refresh_profiles()

    def add_item(self):
        p = self._current_profile()
        if not p: return
        self._search_add_dialog(p)

    def _search_add_dialog(self, profile):
        win = tk.Toplevel(self.root); win.title("添加文件 · 全局搜索"); win.geometry("760x460"); win.transient(self.root); win.grab_set()
        top = ttk.Frame(win, padding=10); top.pack(fill="x")
        query = tk.StringVar(); entry = ttk.Entry(top, textvariable=query, width=55); entry.pack(side="left", fill="x", expand=True); entry.focus_set()
        status = ttk.Label(win, text="输入程序名后点击搜索，支持 exe 和 lnk", padding=(10, 0)); status.pack(anchor="w")
        tree = ttk.Treeview(win, columns=("name", "path"), show="headings", height=15); tree.heading("name", text="名称"); tree.heading("path", text="路径"); tree.column("name", width=220); tree.column("path", width=500); tree.pack(fill="both", expand=True, padx=10, pady=8)
        bottom = ttk.Frame(win, padding=10); bottom.pack(fill="x")
        result_queue = queue.Queue(); searching = [False]

        def scan():
            term = query.get().strip().lower()
            if not term: status.config(text="请输入程序名或关键词"); return
            if searching[0]: return
            searching[0] = True; status.config(text="正在搜索本机文件，请稍候..."); tree.delete(*tree.get_children())
            threading.Thread(target=self._search_files, args=(term, result_queue), daemon=True).start(); win.after(150, poll)

        def poll():
            count = 0
            while not result_queue.empty():
                kind, value = result_queue.get()
                if kind == "item": tree.insert("", "end", values=(Path(value).stem, value)); count += 1
                else: searching[0] = False; status.config(text=f"搜索完成，共找到 {value} 个结果")
            if searching[0]: win.after(150, poll)

        def choose(_event=None):
            selected = tree.selection()
            if not selected: return
            path = tree.item(selected[0], "values")[1]; item = self._item_dialog(AppItem(Path(path).stem, path, delay=len(profile.items)), editing=False)
            if item:
                profile.items.append(item); save_data(self.profiles, self.autostart_var.get()); self._refresh_items(); win.destroy()

        def browse():
            path = filedialog.askopenfilename(parent=win, title="选择快捷方式或程序", filetypes=[("快捷方式/程序", "*.lnk *.exe"), ("所有文件", "*.*")])
            if not path: return
            item = self._item_dialog(AppItem(Path(path).stem, path, delay=len(profile.items)), editing=False)
            if item:
                profile.items.append(item); save_data(self.profiles, self.autostart_var.get()); self._refresh_items(); win.destroy()

        ttk.Button(top, text="搜索", command=scan).pack(side="left", padx=(8, 0)); ttk.Button(top, text="浏览选择文件", command=browse).pack(side="left", padx=6); ttk.Button(bottom, text="取消", command=win.destroy).pack(side="right"); ttk.Button(bottom, text="添加选中项", command=choose).pack(side="right", padx=6)
        tree.bind("<Double-1>", choose); entry.bind("<Return>", lambda _e: scan())

    def _search_files(self, term, result_queue):
        roots = [Path(os.environ.get("ProgramFiles", "C:/Program Files")), Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")), Path(os.environ.get("LOCALAPPDATA", str(Path.home()))), Path(os.environ.get("APPDATA", str(Path.home()))), Path.home() / "Desktop", Path.home() / "Downloads"]
        # Include all mounted Windows drives so portable applications are discoverable too.
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = Path(f"{letter}:\\")
            if drive.exists() and drive not in roots: roots.append(drive)
        seen, count = set(), 0
        skip = {"windows", "$recycle.bin", "system volume information", "node_modules", "__pycache__"}
        for root in roots:
            if not root.exists(): continue
            try:
                for current, dirs, files in os.walk(root, topdown=True):
                    dirs[:] = [d for d in dirs if d.lower() not in skip]
                    for name in files:
                        if not name.lower().endswith((".exe", ".lnk")) or term not in name.lower(): continue
                        path = str(Path(current) / name)
                        if path.lower() not in seen:
                            seen.add(path.lower()); result_queue.put(("item", path)); count += 1
                            if count >= 300: result_queue.put(("done", count)); return
            except (PermissionError, OSError): continue
        result_queue.put(("done", count))

    def edit_item(self):
        p = self._current_profile(); sel = self.item_tree.selection()
        if not p or not sel: return
        idx = self.item_tree.index(sel[0]); item = self._item_dialog(p.items[idx], editing=True)
        if item: p.items[idx] = item; save_data(self.profiles, self.autostart_var.get()); self._refresh_items()

    def remove_item(self):
        p = self._current_profile(); sel = self.item_tree.selection()
        if p and sel:
            del p.items[self.item_tree.index(sel[0])]; save_data(self.profiles, self.autostart_var.get()); self._refresh_items()

    def _item_dialog(self, item, editing=False):
        win = tk.Toplevel(self.root); win.title("编辑应用"); win.transient(self.root); win.grab_set(); win.resizable(False, False)
        fields = {}
        for row, (label, key) in enumerate([("名称", "name"), ("路径", "path"), ("启动参数", "args"), ("延迟秒数", "delay"), ("等待值", "wait_value"), ("超时秒数", "wait_timeout")]):
            ttk.Label(win, text=label + "：").grid(row=row, column=0, sticky="e", padx=8, pady=5)
            ent = ttk.Entry(win, width=52); ent.grid(row=row, column=1, padx=8, pady=5); ent.insert(0, str(getattr(item, key))) ; fields[key] = ent
        ttk.Label(win, text="等待条件：").grid(row=6, column=0, sticky="e", padx=8, pady=5)
        wait_var = tk.StringVar(value=item.wait_type); wait_box = ttk.Combobox(win, textvariable=wait_var, values=["none", "process", "window", "port"], state="readonly", width=49); wait_box.grid(row=6, column=1, padx=8, pady=5)
        ttk.Label(win, text="窗口状态：").grid(row=7, column=0, sticky="e", padx=8, pady=5)
        state_var = tk.StringVar(value=item.window_state); state_box = ttk.Combobox(win, textvariable=state_var, values=["normal", "minimized", "maximized"], state="readonly", width=49); state_box.grid(row=7, column=1, padx=8, pady=5)
        admin_var = tk.BooleanVar(value=item.run_as_admin); skip_var = tk.BooleanVar(value=item.skip_if_running)
        ttk.Checkbutton(win, text="以管理员身份运行（会触发 UAC）", variable=admin_var).grid(row=8, column=1, sticky="w", padx=8, pady=3)
        ttk.Checkbutton(win, text="目标已运行时跳过", variable=skip_var).grid(row=9, column=1, sticky="w", padx=8, pady=3)
        result = [None]
        def ok():
            try:
                result[0] = AppItem(fields["name"].get().strip() or Path(fields["path"].get()).stem, fields["path"].get().strip(), fields["args"].get(), float(fields["delay"].get() or 0), wait_var.get(), fields["wait_value"].get().strip(), int(fields["wait_timeout"].get() or 60), admin_var.get(), skip_var.get(), state_var.get())
                if not result[0].path: raise ValueError()
                win.destroy()
            except ValueError: messagebox.showerror("输入错误", "请检查路径、延迟和超时设置。", parent=win)
        ttk.Button(win, text="确定", command=ok).grid(row=10, column=1, sticky="e", padx=8, pady=10); ttk.Button(win, text="取消", command=win.destroy).grid(row=10, column=1, padx=95, pady=10)
        self.root.wait_window(win); return result[0]

    def start_profile(self):
        if self.running: return messagebox.showinfo("提示", "已有方案正在启动。")
        p = self._current_profile()
        if not p or not p.items: return messagebox.showinfo("提示", "当前方案没有应用。")
        self.running = True; self.stop_event.clear(); self.started_pids = []; self.log(f"开始启动方案：{p.name}")
        threading.Thread(target=self._run_profile, args=(p,), daemon=True).start()

    def _run_profile(self, p):
        try:
            for idx, item in enumerate(p.items):
                if self.stop_event.is_set(): break
                self.log(f"[{idx + 1}/{len(p.items)}] 准备启动：{item.name}")
                if item.skip_if_running and self._is_running(item): self.log(f"跳过（已运行）：{item.name}"); continue
                if item.delay: time.sleep(item.delay)
                pid = self._launch(item)
                if pid: self.started_pids.append(pid); self.log(f"已启动：{item.name} (PID {pid})")
                else: self.log(f"启动失败：{item.name}"); continue
                if item.wait_type != "none":
                    self.log(f"等待 {item.wait_type} 条件：{item.wait_value}")
                    if self._wait_condition(item): self.log(f"等待完成：{item.name}")
                    else: self.log(f"等待超时：{item.name}")
            self.log("方案启动完成" if not self.stop_event.is_set() else "方案启动已停止")
        finally: self.running = False

    def _is_running(self, item):
        name = Path(item.path).name.lower()
        try: return name.endswith(".exe") and subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}"], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout.lower().count(name) > 0
        except Exception: return False

    def _launch(self, item):
        try:
            launch_path = item.path
            launch_args = item.args
            if item.path.lower().endswith(".lnk"):
                # Resolve the shortcut so the target process can be tracked and stopped.
                ps = "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($args[0]); $s.TargetPath; $s.Arguments"
                r = subprocess.run(["powershell", "-NoProfile", "-Command", ps, item.path], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
                lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
                if lines and Path(lines[0]).exists():
                    launch_path = lines[0]
                    launch_args = launch_args or (lines[1] if len(lines) > 1 else "")
                else:
                    os.startfile(item.path)
                    return None
            if item.run_as_admin:
                import ctypes; show = {"minimized": 2, "maximized": 3}.get(item.window_state, 1); r = ctypes.windll.shell32.ShellExecuteW(None, "runas", launch_path, launch_args or None, str(Path(launch_path).parent), show); return int(r) if r > 32 else None
            startupinfo = None
            if item.window_state in ("minimized", "maximized"):
                startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW; startupinfo.wShowWindow = 2 if item.window_state == "minimized" else 3
            proc = subprocess.Popen([launch_path] + (shlex.split(launch_args, posix=False) if launch_args else []), cwd=str(Path(launch_path).parent), creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, startupinfo=startupinfo)
            return proc.pid
        except Exception as e: self.log(f"错误：{e}"); return None

    def _wait_condition(self, item):
        deadline = time.time() + max(1, item.wait_timeout)
        while time.time() < deadline and not self.stop_event.is_set():
            try:
                if item.wait_type == "process" and subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {item.wait_value}"], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout.lower().count(item.wait_value.lower()) > 0: return True
                if item.wait_type == "port":
                    host, port = (item.wait_value.split(":", 1) if ":" in item.wait_value else ("127.0.0.1", item.wait_value))
                    with socket.create_connection((host, int(port)), timeout=0.4): return True
                if item.wait_type == "window" and self._window_exists(item.wait_value): return True
            except Exception: pass
            time.sleep(0.5)
        return False

    def _window_exists(self, title):
        user32 = ctypes.windll.user32; found = [False]; EnumWindows = user32.EnumWindows; EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd); buf = ctypes.create_unicode_buffer(n + 1); user32.GetWindowTextW(hwnd, buf, n + 1)
                if title.lower() in buf.value.lower(): found[0] = True; return False
            return True
        EnumWindows(EnumWindowsProc(cb), 0); return found[0]

    def stop_profile(self):
        self.stop_event.set()
        for pid in self.started_pids:
            try: subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            except Exception: pass
        self.log("已请求停止当前方案")

    def toggle_autostart(self):
        enabled = self.autostart_var.get(); self.autostart = enabled
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            if enabled: winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{sys.executable}" --tray')
            else: winreg.DeleteValue(key, APP_NAME)
            winreg.CloseKey(key); save_data(self.profiles, enabled); self.log("开机自启动已" + ("开启" if enabled else "关闭"))
        except Exception as e: messagebox.showerror("设置失败", str(e))

    def create_profile_shortcut(self):
        profile = self._current_profile()
        if not profile: return messagebox.showinfo("提示", "请先选择一个方案。")
        desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
        path = filedialog.asksaveasfilename(initialdir=str(desktop), initialfile=f"{profile.name}.lnk", defaultextension=".lnk", filetypes=[("Windows 快捷方式", "*.lnk")])
        if not path: return
        target = sys.executable
        if Path(target).name.lower() in ("python.exe", "pythonw.exe"):
            target = str(Path(__file__).with_name("dist") / "OneClickLauncher.exe") if (Path(__file__).with_name("dist") / "OneClickLauncher.exe").exists() else target
        command = "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:ONECLICK_LNK);$s.TargetPath=$env:ONECLICK_TARGET;$s.Arguments='--launch-profile \"'+$env:ONECLICK_PROFILE.Replace('\"','\"\"')+'\"';$s.WorkingDirectory=(Split-Path $env:ONECLICK_TARGET);$s.IconLocation=$env:ONECLICK_TARGET;$s.Save()"
        try:
            env = os.environ.copy(); env.update({"ONECLICK_LNK": path, "ONECLICK_TARGET": target, "ONECLICK_PROFILE": profile.name})
            subprocess.run(["powershell", "-NoProfile", "-Command", command], check=True, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            messagebox.showinfo("创建成功", f"已创建快捷方式：\n{path}")
        except Exception as e: messagebox.showerror("创建失败", str(e))

    def export_config(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path: Path(path).write_text(CONFIG_FILE.read_text(encoding="utf-8"), encoding="utf-8")

    def import_config(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if path:
            try: data = json.loads(Path(path).read_text(encoding="utf-8")); CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"); self.profiles, self.autostart = load_data(); self.autostart_var.set(self.autostart); self._refresh_profiles()
            except Exception as e: messagebox.showerror("导入失败", str(e))

    def _setup_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (64, 64), "#2563eb"); ImageDraw.Draw(img).text((20, 16), "1", fill="white")
            self.tray = pystray.Icon(APP_NAME, img, APP_NAME, pystray.Menu(pystray.MenuItem("显示窗口", self.show_window), pystray.MenuItem("启动当前方案", lambda: self.start_profile()), pystray.MenuItem("退出", self.quit_app)))
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception: self.tray = None

    def _on_minimize(self, _):
        if self.root.state() == "iconic": self.root.after(100, self.hide_to_tray)
    def hide_to_tray(self): self.root.withdraw()
    def show_window(self, *_): self.root.deiconify(); self.root.state("normal"); self.root.lift()
    def quit_app(self, *_):
        self.stop_profile()
        if self.tray: self.tray.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = (TkinterDnD.Tk() if TkinterDnD else tk.Tk()); app = LauncherApp(root)
    if "--launch-profile" in sys.argv:
        try:
            profile_name = sys.argv[sys.argv.index("--launch-profile") + 1]
            for index, profile in enumerate(app.profiles):
                if profile.name == profile_name:
                    app.profile_list.selection_clear(0, "end"); app.profile_list.selection_set(index); app.profile_list.activate(index); app._refresh_items(); break
            root.withdraw(); app.start_profile()
            def close_when_done():
                if app.running: root.after(200, close_when_done)
                else: root.destroy()
            root.after(200, close_when_done)
        except (IndexError, ValueError):
            root.destroy()
    elif "--tray" in sys.argv: root.withdraw()
    root.mainloop()
