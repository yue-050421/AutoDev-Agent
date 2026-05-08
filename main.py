#!/usr/bin/env python3
import json
import uuid

# 导入其它模块的组件
from config import client, MODEL, WORKDIR, SKILLS_DIR, TOKEN_THRESHOLD, VALID_MSG_TYPES
from core_sandbox import run_bash, run_read, run_write, run_edit
from memory import estimate_tokens, microcompact, auto_compact
from managers import (
    TodoManager, SkillLoader, TaskManager, BackgroundManager,
    MessageBus, TeammateManager, run_subagent
)

# === 全局实例 ===
TODO = TodoManager()
SKILLS = SkillLoader(SKILLS_DIR)
TASK_MGR = TaskManager()
BG = BackgroundManager()
BUS = MessageBus()
TEAM = TeammateManager(BUS, TASK_MGR)

# === 系统提示词 ===
SYSTEM = f"""You are a coding agent at {WORKDIR}. Use tools to solve tasks.
Prefer task_create/task_update/task_list for multi-step work. Use TodoWrite for short checklists.
Use task for subagent delegation. Use load_skill for specialized knowledge.
Skills: {SKILLS.descriptions()}
Before sending a message to a teammate, use list_teammates to check their status. If their status is 'shutdown', you MUST use spawn_teammate to wake them up before expecting a reply."""

# === 关机与计划审批 ===
shutdown_requests = {}


def handle_shutdown_request(teammate: str) -> str:
    req_id = str(uuid.uuid4())[:8]
    shutdown_requests[req_id] = {"target": teammate, "status": "pending"}
    BUS.send("lead", teammate, "Please shut down.", "shutdown_request", {"request_id": req_id})
    return f"Shutdown request {req_id} sent to '{teammate}'"


def handle_plan_review(request_id: str, approve: bool, feedback: str = "") -> str:
    req = TEAM.plan_requests.get(request_id)
    if not req: return f"Error: Unknown plan request_id '{request_id}'"
    req["status"] = "approved" if approve else "rejected"
    BUS.send("lead", req["from"], feedback, "plan_approval_response",
             {"request_id": request_id, "approve": approve, "feedback": feedback})
    return f"Plan {req['status']} for '{req['from']}'"


# === 工具路由分发 ===
TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"], kw.get("tool_use_id", "")),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("tool_use_id", ""), kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "TodoWrite": lambda **kw: TODO.update(kw["items"]),
    "task": lambda **kw: run_subagent(kw["prompt"], kw.get("agent_type", "Explore")),
    "load_skill": lambda **kw: SKILLS.load(kw["name"]),
    "compress": lambda **kw: "Compressing...",
    "background_run": lambda **kw: BG.run(kw["command"], kw.get("timeout", 120)),
    "check_background": lambda **kw: BG.check(kw.get("task_id")),
    "task_create": lambda **kw: TASK_MGR.create(kw["subject"], kw.get("description", "")),
    "task_get": lambda **kw: TASK_MGR.get(kw["task_id"]),
    "task_update": lambda **kw: TASK_MGR.update(kw["task_id"], kw.get("status"), kw.get("add_blocked_by"),
                                                kw.get("add_blocks")),
    "task_list": lambda **kw: TASK_MGR.list_all(),
    "spawn_teammate": lambda **kw: TEAM.spawn(kw["name"], kw["role"], kw["prompt"]),
    "list_teammates": lambda **kw: TEAM.list_all(),
    "send_message": lambda **kw: BUS.send("lead", kw["to"], kw["content"], kw.get("msg_type", "message")),
    "read_inbox": lambda **kw: json.dumps(BUS.read_inbox("lead"), indent=2),
    "broadcast": lambda **kw: BUS.broadcast("lead", kw["content"], TEAM.member_names()),
    "shutdown_request": lambda **kw: handle_shutdown_request(kw["teammate"]),
    "plan_approval": lambda **kw: handle_plan_review(kw["request_id"], kw["approve"], kw.get("feedback", "")),
    "idle": lambda **kw: "Lead does not idle.",
    "claim_task": lambda **kw: TASK_MGR.claim(kw["task_id"], "lead"),
}

# === 工具定义 ===
TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
                      "required": ["path"]}},
    {"name": "write_file", "description": "Write content to file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                      "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in file.", "input_schema": {"type": "object",
                                                                                         "properties": {
                                                                                             "path": {"type": "string"},
                                                                                             "old_text": {
                                                                                                 "type": "string"},
                                                                                             "new_text": {
                                                                                                 "type": "string"}},
                                                                                         "required": ["path",
                                                                                                      "old_text",
                                                                                                      "new_text"]}},
    {"name": "TodoWrite", "description": "Update task tracking list.", "input_schema": {"type": "object",
                                                                                        "properties": {
                                                                                            "items": {"type": "array",
                                                                                                      "items": {
                                                                                                          "type": "object",
                                                                                                          "properties": {
                                                                                                              "content": {
                                                                                                                  "type": "string"},
                                                                                                              "status": {
                                                                                                                  "type": "string",
                                                                                                                  "enum": [
                                                                                                                      "pending",
                                                                                                                      "in_progress",
                                                                                                                      "completed"]},
                                                                                                              "activeForm": {
                                                                                                                  "type": "string"}},
                                                                                                          "required": [
                                                                                                              "content",
                                                                                                              "status",
                                                                                                              "activeForm"]}}},
                                                                                        "required": ["items"]}},
    {"name": "task", "description": "Spawn a subagent for isolated exploration or work.",
     "input_schema": {"type": "object", "properties": {"prompt": {"type": "string"}, "agent_type": {"type": "string",
                                                                                                    "enum": ["Explore",
                                                                                                             "general-purpose"]}},
                      "required": ["prompt"]}},
    {"name": "load_skill", "description": "Load specialized knowledge by name.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "compress", "description": "Manually compress conversation context.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "background_run", "description": "Run command in background thread.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}},
                      "required": ["command"]}},
    {"name": "check_background", "description": "Check background task status.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}}}},
    {"name": "task_create", "description": "Create a persistent file task.", "input_schema": {"type": "object",
                                                                                              "properties": {
                                                                                                  "subject": {
                                                                                                      "type": "string"},
                                                                                                  "description": {
                                                                                                      "type": "string"}},
                                                                                              "required": ["subject"]}},
    {"name": "task_get", "description": "Get task details by ID.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
    {"name": "task_update", "description": "Update task status or dependencies.", "input_schema": {"type": "object",
                                                                                                   "properties": {
                                                                                                       "task_id": {
                                                                                                           "type": "integer"},
                                                                                                       "status": {
                                                                                                           "type": "string",
                                                                                                           "enum": [
                                                                                                               "pending",
                                                                                                               "in_progress",
                                                                                                               "completed",
                                                                                                               "deleted"]},
                                                                                                       "add_blocked_by": {
                                                                                                           "type": "array",
                                                                                                           "items": {
                                                                                                               "type": "integer"}},
                                                                                                       "add_blocks": {
                                                                                                           "type": "array",
                                                                                                           "items": {
                                                                                                               "type": "integer"}}},
                                                                                                   "required": [
                                                                                                       "task_id"]}},
    {"name": "task_list", "description": "List all tasks.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "spawn_teammate", "description": "Spawn a persistent autonomous teammate.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"},
                                                       "prompt": {"type": "string"}},
                      "required": ["name", "role", "prompt"]}},
    {"name": "list_teammates", "description": "List all teammates.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "send_message", "description": "Send a message to a teammate.", "input_schema": {"type": "object",
                                                                                              "properties": {"to": {
                                                                                                  "type": "string"},
                                                                                                             "content": {
                                                                                                                 "type": "string"},
                                                                                                             "msg_type": {
                                                                                                                 "type": "string",
                                                                                                                 "enum": list(
                                                                                                                     VALID_MSG_TYPES)}},
                                                                                              "required": ["to",
                                                                                                           "content"]}},
    {"name": "read_inbox", "description": "Read and drain the lead's inbox.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "broadcast", "description": "Send message to all teammates.",
     "input_schema": {"type": "object", "properties": {"content": {"type": "string"}}, "required": ["content"]}},
    {"name": "shutdown_request", "description": "Request a teammate to shut down.",
     "input_schema": {"type": "object", "properties": {"teammate": {"type": "string"}}, "required": ["teammate"]}},
    {"name": "plan_approval", "description": "Approve or reject a teammate's plan.", "input_schema": {"type": "object",
                                                                                                      "properties": {
                                                                                                          "request_id": {
                                                                                                              "type": "string"},
                                                                                                          "approve": {
                                                                                                              "type": "boolean"},
                                                                                                          "feedback": {
                                                                                                              "type": "string"}},
                                                                                                      "required": [
                                                                                                          "request_id",
                                                                                                          "approve"]}},
    {"name": "idle", "description": "Enter idle state.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "claim_task", "description": "Claim a task from the board.",
     "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}},
]

# === Agent主循环 ===
def agent_loop(messages: list):
    rounds_without_todo = 0
    while True:
        microcompact(messages)
        if estimate_tokens(messages) > TOKEN_THRESHOLD:
            print("[auto-compact triggered]")
            messages[:] = auto_compact(messages)

        # 将后台通知和邮件，无缝追加到当前最后一个 User 消息的内容中
        # 绝不强行插入假的 Assistant 消息打乱 API 的 tool_result 校验链条
        def _inject_notice(notice_text: str):
            if not messages:
                messages.append({"role": "user", "content": notice_text})
                return
            last_msg = messages[-1]
            if last_msg["role"] == "user":
                if isinstance(last_msg["content"], list):
                    # 如果上一条包含工具结果列表，追加一个文本块
                    last_msg["content"].append({"type": "text", "text": notice_text})
                else:
                    # 如果是普通纯文本，直接拼在下面
                    last_msg["content"] = str(last_msg["content"]) + "\n\n" + notice_text
            else:
                messages.append({"role": "user", "content": notice_text})

        notifs = BG.drain()
        if notifs:
            txt = "\n".join(f"[bg:{n['task_id']}] {n['status']}: {n['result']}" for n in notifs)
            _inject_notice(f"<background-results>\n{txt}\n</background-results>")

        inbox = BUS.read_inbox("lead")
        if inbox:
            _inject_notice(f"<inbox>{json.dumps(inbox, indent=2)}</inbox>")

        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        
        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"\033[32mAgent: {block.text}\033[0m")

        if response.stop_reason != "tool_use":
            return

        results = []
        used_todo = False
        manual_compress = False
        compact_focus = None

        for block in response.content:
            if block.type == "tool_use":
                if block.name == "compress":
                    manual_compress = True
                    compact_focus = (block.input or {}).get("focus")
                handler = TOOL_HANDLERS.get(block.name)
                try:
                    tool_input = dict(block.input or {})
                    tool_input["tool_use_id"] = block.id
                    output = handler(**tool_input) if handler else f"Unknown tool: {block.name}"
                except Exception as e:
                    output = f"Error: {e}"
                print(f"> {block.name}: {str(output)[:200]}")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})
                if block.name == "TodoWrite":
                    used_todo = True

        rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
        if TODO.has_open_items() and rounds_without_todo >= 3:
            results.append({"type": "text", "text": "<reminder>Update your todos.</reminder>"})
        messages.append({"role": "user", "content": results})

        if manual_compress:
            print("[manual compact]")
            messages[:] = auto_compact(messages, focus=compact_focus)

# === REPL 控制台 ===
if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[36magtnt >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        if query.strip() == "/compact":
            if history:
                print("[manual compact via /compact]")
                history[:] = auto_compact(history)
            continue
        if query.strip() == "/tasks":
            print(TASK_MGR.list_all())
            continue
        if query.strip() == "/team":
            print(TEAM.list_all())
            continue
        if query.strip() == "/inbox":
            print(json.dumps(BUS.read_inbox("lead"), indent=2))
            continue

        history.append({"role": "user", "content": query})
        agent_loop(history)
        print()
