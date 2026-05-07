##统计Token，做深层或浅层的记忆压缩
import json
import time

# 从配置层引入大模型客户端
from config import client, MODEL, TRANSCRIPT_DIR, KEEP_RECENT, PRESERVE_RESULT_TOOLS

def estimate_tokens(messages: list) -> int:
    return len(json.dumps(messages, default=str)) // 4

#浅层压缩
def microcompact(messages: list):
    tool_results = []
    for msg in messages:
        if msg["role"] == "user" and isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    tool_results.append(part)
    if len(tool_results) <= KEEP_RECENT:
        return
    tool_name_map = {}#构建工具ID->工具名称的映射表
    for msg in messages:
        if msg["role"] == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if hasattr(block, "type") and block.type == "tool_use":
                        tool_name_map[block.id] = block.name
    for part in tool_results[:-KEEP_RECENT]:
        if not isinstance(part.get("content"), str) or len(part["content"]) <= 100:
            continue
        tool_id = part.get("tool_use_id", "")
        tool_name = tool_name_map.get(tool_id, "unknown")
        if tool_name in PRESERVE_RESULT_TOOLS:
            continue
        part["content"] = f"[较早的输出内容已折叠，使用了工具 {tool_name}]"
        
#深层压缩函数
def auto_compact(messages: list, focus: str = None) -> list:
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")
    conv_text = json.dumps(messages, default=str)[:80000]
    prompt = (
        "你是一个系统的底层记忆压缩模块。请将以下冗长的对话历史总结为一份高浓缩的中文记忆上下文。\n"
        "你的总结必须结构化，包含以下要素：\n"
        "1) 核心任务与背景：用户当前的大目标是什么？\n"
        "2) 已经完成的动作：操作了哪些文件？调用了哪些工具？产出了什么结果？（仅陈述事实，不要带有待办语气）\n"
        "3) 重要的约束与偏好：用户强调过哪些关键信息？\n"
        "4) 当前所在的断点：在压缩之前，Agent 正在处理什么具体问题？\n"
        "请保持客观和简练，只保留能帮助 Agent 继续执行任务的关键信息。\n"
    )
    if focus:
        prompt += f"\n请特别关注并保留关于此部分的信息: {focus}\n"
    resp = client.messages.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt + "\n\n这是完整的对话记录：\n" + conv_text}],
        max_tokens=4000,
    )
    summary = resp.content[0].text
    continuation = (
        "【系统通知：由于对话上下文过长，前期的历史对话已被清理并压缩。以下是前期对话的核心记忆摘要。】\n\n"
        f"{summary}\n\n"
        "【重要指令】：请基于上述摘要理解当前的业务背景。你不需要向用户汇报上述已完成的内容，直接继续回答或执行用户在记忆压缩前下达的最后一道指令即可。"
    )
    return [{"role": "user", "content": continuation}]
