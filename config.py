##配置层，存放环境变量，大模型客户端初始化以及所有全局常量

import os
from pathlib import Path
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

TEAM_DIR = WORKDIR / ".team"
INBOX_DIR = TEAM_DIR / "inbox"
TASKS_DIR = WORKDIR / ".tasks"
SKILLS_DIR = WORKDIR / "skills"
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOKEN_THRESHOLD = 100000
POLL_INTERVAL = 5
IDLE_TIMEOUT = 60

# 持久化输出机制配置
TASK_OUTPUT_DIR = WORKDIR / ".task_outputs"
TOOL_RESULTS_DIR = TASK_OUTPUT_DIR / "tool-results"
PERSIST_OUTPUT_TRIGGER_CHARS_DEFAULT = 50000
PERSIST_OUTPUT_TRIGGER_CHARS_BASH = 30000
CONTEXT_TRUNCATE_CHARS = 50000
PERSISTED_OPEN = "<persisted-output>"
PERSISTED_CLOSE = "</persisted-output>"
PERSISTED_PREVIEW_CHARS = 2000
KEEP_RECENT = 3
PRESERVE_RESULT_TOOLS = {"read_file"}

VALID_MSG_TYPES = {"message", "broadcast", "shutdown_request",
                   "shutdown_response", "plan_approval_response"}