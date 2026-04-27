 AutoDev Agent

这是一个基于大语言模型 (LLM) 和 Linux bwrap 底层隔离技术的智能编码与自动化测试沙箱平台。

🌟 核心特性
- 底层安全沙箱：基于 Linux Namespace 实现物理隔离，阻断恶意代码。
- 高并发与容错：采用 SQLite WAL 解决死锁，实现 95% 资源熔断。
- 长程记忆压缩：引入动态 Token 压缩，解决长日志爆显存问题。

🛠️ 技术栈
Python 3.12 | Linux Bubblewrap | SQLite | Anthropic API | Multi-Agent
