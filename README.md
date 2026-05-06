 AutoDev Agent

这是一个基于大语言模型 (LLM) 和 Linux bwrap 底层隔离技术的智能编码与自动化测试沙箱平台。

🌟 核心特性
- 底层安全沙箱：基于 Linux Namespace 实现物理隔离，阻断恶意代码。
- 长程记忆压缩：引入动态 Token 压缩，解决长日志爆显存问题。
- 多智能体总线：基于 MessageBus 构建发布/订阅机制，支持多 Agent 并行调度与跨进程代码协同。

🛠️ 技术栈
Python 3.12 | Linux Bubblewrap | SQLite | Anthropic API | Multi-Agent
