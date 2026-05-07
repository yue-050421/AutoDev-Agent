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

TEAM_DIR = WORKDIR / ".team"  ##存放多智能体团队相关的元数据和配置
INBOX_DIR = TEAM_DIR / "inbox"#系统消息总线
TASKS_DIR = WORKDIR / ".tasks"#全局任务板
SKILLS_DIR = WORKDIR / "skills"#技能库
TRANSCRIPT_DIR = WORKDIR / ".transcripts"#记忆档案馆
TOKEN_THRESHOLD = 100000#触发深度记忆压缩的阈值
POLL_INTERVAL = 5#轮询间隔，空闲子Agent会每隔五秒检查一次自己的INBOX_DIR(是否有新消息）和TASKS_DIR（是否有无人认领的新任务）
IDLE_TIMEOUT = 60#闲置超时时间，如果一个子Agent连续60s没有信息也没有任务，会自动关机以释放资源

# 持久化输出机制配置
TASK_OUTPUT_DIR = WORKDIR / ".task_outputs"
TOOL_RESULTS_DIR = TASK_OUTPUT_DIR / "tool-results"
PERSIST_OUTPUT_TRIGGER_CHARS_DEFAULT = 50000#普通工具输出触发写盘的阈值
PERSIST_OUTPUT_TRIGGER_CHARS_BASH = 30000#Bash命令阈值更严格
CONTEXT_TRUNCATE_CHARS = 50000#绝对的硬阶段闲置，即使经过了各种处理，返回给大模型的文本块也不能超过50000
PERSISTED_OPEN = "<persisted-output>"
PERSISTED_CLOSE = "</persisted-output>"
PERSISTED_PREVIEW_CHARS = 2000
KEEP_RECENT = 3#浅层记忆压缩，系统只保留最近3次工具调用的完整输出
PRESERVE_RESULT_TOOLS = {"read_file"}#豁免名单

VALID_MSG_TYPES = {"message", "broadcast", "shutdown_request",
                   "shutdown_response", "plan_approval_response"}#定义了消息总线中允许流通的信件类型 
#message 点对点私聊 broadcast:广播给全体队友 shutdown_request/response:关机握手协议
#plan_approval_response:审批流响应
