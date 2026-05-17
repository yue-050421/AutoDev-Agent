import re
import os
import subprocess
import threading
import time
import uuid
import sqlite3
import resource
from pathlib import Path
import psutil

# 从配置中心引入确实存在的常量，移除会引发 ImportError 的不存在变量
from config import (
    WORKDIR,
    TOOL_RESULTS_DIR,
    PERSIST_OUTPUT_TRIGGER_CHARS_DEFAULT,
    PERSIST_OUTPUT_TRIGGER_CHARS_BASH,
    CONTEXT_TRUNCATE_CHARS,
    PERSISTED_OPEN,
    PERSISTED_CLOSE,
    PERSISTED_PREVIEW_CHARS,
    IDLE_TIMEOUT,
)

# 沙箱特有的资源限制配置（直接在这里定义，不依赖 config.py）
COMMAND_TIMEOUT_SECONDS = 120#任何命令最多跑120s
MAX_OUTPUT_BYTES = 10 * 1024 * 1024#标准输出最多为10MB

# 确保核心物理目录存在
WORKDIR.mkdir(parents=True, exist_ok=True)
TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# === SECTION: 大输出拦截与硬盘持久化 ===
def _persist_tool_result(tool_use_id: str, content: str) -> Path:
    #将工具调用的结果持久化到固定目录下的文本文件中
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", tool_use_id or "unknown")
    path = TOOL_RESULTS_DIR / f"{safe_id}.txt"
    if not path.exists():
        path.write_text(content)
    return path.relative_to(WORKDIR)

def _format_size(size: int) -> str:
#将一个整数转换成人类可读的带单位字符串
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"

def _preview_slice(text: str, limit: int) -> tuple[str, bool]:
    #生成长文本的智能预览
    if len(text) <= limit:
        return text, False
    head_limit = int(limit * 0.25)
    tail_limit = limit - head_limit
    preview = text[:head_limit] + "\n\n... [中间内容已截断] ...\n\n" + text[-tail_limit:]
    return preview, True

def _build_persisted_marker(stored_path: Path, content: str) -> str:
    #生成摘要标记给大语言模型
    preview, has_more = _preview_slice(content, PERSISTED_PREVIEW_CHARS)
    marker = (
        f"{PERSISTED_OPEN}\n"
        f"Output too large ({_format_size(len(content))}). "
        f"Full output saved to: {stored_path}\n\n"
        f"Preview (limit {_format_size(PERSISTED_PREVIEW_CHARS)}):\n"
        f"{preview}"
    )
    if has_more:
        marker += "\n..."
    marker += f"\n{PERSISTED_CLOSE}"
    return marker

def maybe_persist_output(tool_use_id: str, output: str, trigger_chars: int = None) -> str:
    #根据输出大小决定是否将完整结果写入磁盘
    if not isinstance(output, str):
        return str(output)
    trigger = PERSIST_OUTPUT_TRIGGER_CHARS_DEFAULT if trigger_chars is None else int(trigger_chars)
    if len(output) <= trigger:
        return output
    stored_path = _persist_tool_result(tool_use_id, output)
    return _build_persisted_marker(stored_path, output)

# === SECTION: 沙箱管理器 (含心跳与状态机) ===
class SandboxManager:
    def __init__(self):
        self.db_path = WORKDIR / ".sandbox_meta.db"
        self.active_instances = {}  # 维护沙箱实例状态机的内存池
        self._init_db()
        
        # 启动后台守护线程，执行 500ms 双链路心跳检测
        self.monitor_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)#线程启动后将执行实例的_heartbeat_loop方法
        self.monitor_thread.start()

    def _init_db(self):#初始化沙箱管理的数据库
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")##日志模式设置为WAL
            # 建立完整的沙箱生命周期表
            conn.execute('''
                CREATE TABLE IF NOT EXISTS sandboxes (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    status TEXT,  -- 空闲(idle), 运行中(working), 异常(exception), 已销毁(destroyed)
                    pid INTEGER,
                    created_at REAL,
                    last_heartbeat REAL
                )
            ''')
            cursor = conn.cursor()
            # 查出所有上一次没来得及正常销毁的沙箱记录
            cursor.execute("SELECT id, pid FROM sandboxes WHERE status IN ('working', 'idle')")
            stale_sandboxes = cursor.fetchall()

            if stale_sandboxes:
                print(f"\n[Sandbox Boot] 检测到 {len(stale_sandboxes)} 个上一次运行残留的孤儿沙箱，开始清理...")
                
                for sid, pid in stale_sandboxes:
                    if pid:
                        try:
                            # 探测并强制杀死滞留在 OS 中的旧沙箱进程
                            proc = psutil.Process(pid)
                            proc.kill()
                            print(f" -> 已强制熔断残留沙箱实例: {sid} (PID: {pid})")
                        except psutil.NoSuchProcess:
                            # 进程在断电或重启中已经死了，那正好
                            pass
                        except Exception as e:
                            print(f" -> 清理沙箱 {sid} (PID: {pid}) 时发生异常: {e}")
                
                # 一键将数据库里这些“前世沙箱”的状态批量重置为异常/已销毁状态
                conn.execute(
                    "UPDATE sandboxes SET status = 'exception', last_heartbeat = ? WHERE status IN ('working', 'idle')",
                    (time.time(),)
                )
                conn.commit()
                print("[Sandbox Boot] 历史孤儿沙箱进程清理完毕，系统已恢复纯净环境。\n")
        finally:
            conn.close()

    def _mark_status(self, sid: str, status: str):#sid:沙箱ID,status:要设置的新状态
        """状态机流转"""#更新沙箱状态
        if sid in self.active_instances:
            self.active_instances[sid]["status"] = status
        try:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.execute("UPDATE sandboxes SET status = ?, last_heartbeat = ? WHERE id = ?", 
                         (status, time.time(), sid))
            conn.commit()
            conn.close()
        except Exception:
            pass

    def _heartbeat_loop(self):
        """双链路心跳保活与分级资源限流"""
        while True:
            time.sleep(0.5)  # 每 500ms 探测一次
            instances = list(self.active_instances.items())#获取self.active_instances字典的当前快照
            
            current_time = time.time()#记录当前时间戳
            for sid, instance in instances:
                if instance["status"] != "working":
                    continue
                
                try:
                    # 1. 尝试获取底层进程的心跳指标
                    # 不再每次重新实例化 Process，而是直接复用内存池里的缓存对象
                    proc = instance.get("proc_obj")
                    if not proc:
                        continue

                    # 因为复用了对象，所以能在 500ms 间隔内准确计算出这段时间的时间片消耗
                    cpu_usage = proc.cpu_percent()
                    mem_usage = proc.memory_percent()
                    
                    # 2. 分级限流策略 (80% 降级，95% 熔断)
                    if cpu_usage > 95.0 or mem_usage > 95.0:
                        # 资源被占满导致假死，强制熔断
                        proc.kill() 
                        self._mark_status(sid, "exception")
                        print(f"\n[Sandbox Alert] 实例 {sid} 资源超限(>95%)，已强制熔断！")
                    elif cpu_usage > 80.0 or mem_usage > 80.0:
                        # 超过80%，限制调度优先级做限流 (Nice 降权)
                        try:
                            proc.nice(19)
                        except Exception:
                            pass
                            
                    # 更新最后心跳时间
                    self.active_instances[sid]["last_heartbeat"] = current_time
                    
                except psutil.NoSuchProcess:
                    # 进程自然结束或异常崩溃
                    pass

    def _set_limits(self):
        """兜底的操作系统级限制"""
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (COMMAND_TIMEOUT_SECONDS, COMMAND_TIMEOUT_SECONDS))
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 * 1024 * 1024, 2 * 1024 * 1024 * 1024))
            resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))
        except Exception:
            pass

    def execute_command(self, command: str, project_path: str, tool_use_id: str = "") -> str:
        session_id = str(threading.get_ident())
        sid = f"sbx_{uuid.uuid4().hex[:8]}"
        
        try:
            proj_root = Path(project_path).resolve()#解析为绝对路径
            
            bwrap_cmd = [
                "bwrap",
                "--unshare-all", "--share-net",
                "--ro-bind", "/usr", "/usr",
                "--ro-bind-try", "/bin", "/bin", "--ro-bind-try", "/sbin", "/sbin",
                "--ro-bind-try", "/lib", "/lib", "--ro-bind-try", "/lib64", "/lib64",
                "--ro-bind-try", "/etc", "/etc",
                "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
                "--bind", str(proj_root), str(proj_root), 
                "--chdir", str(proj_root),                
                "--setenv", "HOME", str(proj_root),
                "bash", "-c", command
            ]

            process = subprocess.Popen(
                bwrap_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                preexec_fn=self._set_limits,
                cwd=proj_root
            )

            # [核心修复]：创建进程后，立刻实例化 psutil.Process 并调用一次，建立时间片基准
            try:
                proc_obj = psutil.Process(process.pid)
                proc_obj.cpu_percent()  # 建立第一次调用的基准点
            except psutil.NoSuchProcess:
                proc_obj = None

            # 注册到状态机池，标记为 working，并将进程对象塞入缓存
            self.active_instances[sid] = {
                "session_id": session_id,
                "pid": process.pid,
                "proc_obj": proc_obj,  # 缓存对象供心跳线程复用
                "status": "working",
                "last_heartbeat": time.time()
            }
            
            try:
                # 初始化 SQLite 记录
                conn = sqlite3.connect(self.db_path, timeout=5.0)
                conn.execute("INSERT INTO sandboxes (id, session_id, status, pid, created_at, last_heartbeat) VALUES (?, ?, ?, ?, ?, ?)",
                             (sid, session_id, "working", process.pid, time.time(), time.time()))
                conn.commit()
                conn.close()
            except Exception:
                pass

            start_time = time.time()
            try:
                # 兜底超时检测
                stdout, stderr = process.communicate(timeout=COMMAND_TIMEOUT_SECONDS + 5)
                exit_code = process.returncode
                
                # 检查是否被心跳线程标记为异常
                if self.active_instances.get(sid, {}).get("status") == "exception":
                    exit_code = -1
                    stderr = (stderr or "") + "\n[ERROR] 被沙箱心跳监控强制熔断 (资源利用率超 95%)"

            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                exit_code = -1
                stderr = (stderr or "") + f"\n[ERROR] Command timed out after {COMMAND_TIMEOUT_SECONDS} seconds"
                self._mark_status(sid, "exception")

            # 正常结束，状态流转为已销毁
            if self.active_instances.get(sid, {}).get("status") != "exception":
                self._mark_status(sid, "destroyed")

            output = (stdout + stderr).strip()
            if not output:
                output = f"[Sandbox {sid}] Command finished with exit code {exit_code} (no output)"

            output = maybe_persist_output(tool_use_id, output, trigger_chars=PERSIST_OUTPUT_TRIGGER_CHARS_BASH)
            if len(output) > CONTEXT_TRUNCATE_CHARS:
                output = output[:CONTEXT_TRUNCATE_CHARS] + "\n... (truncated)"
            return output
            
        except Exception as e:
            self._mark_status(sid, "exception")
            return f"Sandbox Exception: {str(e)}"
        finally:
            # 清理内存池
            if sid in self.active_instances:
                del self.active_instances[sid]
                    
SANDBOX = SandboxManager()

def run_bash(command: str, tool_use_id: str = "") -> str:
    return SANDBOX.execute_command(command, str(WORKDIR), tool_use_id)

# === SECTION: 文件读写工具 ===

def _resolve_target_path(p: str) -> Path:
    base_workdir = WORKDIR.resolve()
    raw_path = Path(p)

    # 取消会话隔离层，直接解析到物理层 WORKDIR
    if raw_path.is_absolute():
        target = raw_path.resolve()
    else:
        target = (base_workdir / raw_path).resolve()

    # 安全性校验：只能操作 WORKDIR 及以下的文件，防止越权访问 /etc 等宿主目录
    if not target.is_relative_to(base_workdir):
        raise ValueError(f"Security Error: Path {target} escapes physical workspace {base_workdir}.")
        
    return target

def run_read(path: str, tool_use_id: str = "", limit: int = None) -> str:
    try:
        target = _resolve_target_path(path)
        if not target.exists():
            return f"Error: File not found: {path}"
        lines = target.read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        out = "\n".join(lines)
        out = maybe_persist_output(tool_use_id, out)
        return out[:CONTEXT_TRUNCATE_CHARS] if isinstance(out, str) else str(out)[:CONTEXT_TRUNCATE_CHARS]
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    try:
        target = _resolve_target_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"Wrote {len(content)} bytes directly to physical path: {target}"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        target = _resolve_target_path(path)
        if not target.exists():
            return f"Error: File not found: {path}"
        content = target.read_text()
        if old_text not in content:
            return f"Error: Text not found in {path}"
        new_content = content.replace(old_text, new_text, 1)
        target.write_text(new_content)
        return f"Edited physical file: {target}"
    except Exception as e:
        return f"Error: {e}"
