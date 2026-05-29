# 🚀 notion-2api: 将 Notion AI 转换为私有 OpenAI API

![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)
![Stars](https://img.shields.io/github/stars/lzA6/notion-2api?style=social)
![Issues](https://img.shields.io/github/issues/lzA6/notion-2api)
![Forks](https://img.shields.io/github/forks/lzA6/notion-2api)

> "我们并非在创造工具，而是在延伸自我。每一次代码的敲击，都是对世界的一次温柔的重塑。" —— lzA6 (AI 构想)

欢迎来到 `notion-2api` 的世界！这是一个能将你强大的 Notion AI 体验，无缝转换为兼容 OpenAI 格式的 API 服务的神奇项目。这意味着，你可以将 Notion AI 作为后端，驱动任何支持 OpenAI API 的应用程序、脚本或服务。

**English Readme Coming Soon!**

---

## 📋 目录

- [✨ 项目亮点](#-项目亮点)
- [📂 项目结构](#-项目结构)
- [🤔 工作原理](#-工作原理)
- [🛠️ 技术栈](#️-技术栈)
- [📖 使用教程](#-使用教程)
- [🔗 一键部署](#-一键部署-规划中)
- [🧠 源码解析](#-源码解析)
- [📊 项目评估](#-项目评估)
- [🗺️ 未来规划](#️-未来规划)
- [🤖 AI 开发者指南](#-ai-开发者指南)
- [💖 贡献指南](#-贡献指南)

---

## ✨ 项目亮点

- **无缝转换**: 将 Notion AI 的非官方接口完美伪装成标准的 OpenAI `v1/chat/completions` 接口
- **多模型支持**: 支持 Notion AI 后端的所有可用模型（以 Notion 实际提供为准）
- **流式响应**: 完全支持 `stream=true`，提供流畅的打字机体验
- **Docker 化部署**: 一行命令轻松启动，告别繁琐环境配置
- **高性能**: 基于 FastAPI 和 Uvicorn，提供稳定高效的异步处理能力
- **智能反爬**: 内置 `cloudscraper`，绕过 Cloudflare 防护，提高请求成功率
- **灵活配置**: 通过简单的 `.env` 文件管理所有凭证和配置

---

## 📂 项目结构

```
notion-2api/
├── .env                    # 环境配置文件（生产环境）
├── .env.example            # 环境配置模板
├── Dockerfile              # Docker 镜像构建文件
├── docker-compose.yml      # Docker Compose 编排文件
├── main.py                 # FastAPI 应用主入口
├── nginx.conf.template     # Nginx 反向代理配置模板
├── requirements.txt        # Python 依赖列表
└── app/
    ├── core/
    │   ├── __init__.py
    │   └── config.py       # Pydantic 配置模型
    ├── providers/
    │   ├── __init__.py
    │   ├── base_provider.py # 抽象基类
    │   └── notion_provider.py # Notion API 交互核心
    └── utils/
        └── sse_utils.py    # Server-Sent Events 工具
```

---

## 🤔 工作原理

`notion-2api` 充当了 OpenAI API 和 Notion AI 之间的翻译官，将两种不同的协议进行无缝转换。

### 架构流程图

```mermaid
graph TD
    A[客户端应用] --> B[Nginx 反向代理]
    B --> C[FastAPI 应用]
    C --> D[认证中间件]
    D --> E[Notion Provider]
    E --> F[Cloudscraper]
    F --> G[Notion AI API]
    G --> H[NDJSON 流解析]
    H --> I[SSE 格式转换]
    I --> C
    C --> B
    B --> A
```

### 数据流详解

1. **请求接收**: 客户端发送标准的 OpenAI API 格式请求
2. **协议转换**: FastAPI 将请求转换为 Notion AI 能理解的格式
3. **会话管理**: 创建 Notion 对话线程并维持会话状态
4. **流式处理**: 实时解析 Notion 返回的 NDJSON 数据流
5. **格式转换**: 将解析后的数据转换为 OpenAI 兼容的 SSE 格式
6. **响应返回**: 通过流式响应将数据实时返回给客户端

---

## 🛠️ 技术栈

| 技术组件 | 版本/类型 | 用途说明 |
|---------|-----------|----------|
| **FastAPI** | 0.104+ | 现代化异步 Web 框架，提供高性能 API 服务 |
| **Uvicorn** | 0.24+ | ASGI 服务器，用于运行 FastAPI 应用 |
| **Cloudscraper** | 最新版 | 绕过 Cloudflare 防护，确保稳定连接 |
| **Pydantic** | 2.0+ | 数据验证和设置管理 |
| **Docker** | 最新版 | 容器化部署和环境隔离 |
| **Nginx** | 最新版 | 反向代理和负载均衡 |
| **Python** | 3.8+ | 主要编程语言 |

---

## 📖 使用教程

### 环境准备

1. **安装 Docker**: 访问 [Docker 官网](https://www.docker.com/get-started) 下载并安装
2. **安装 Git**: 用于克隆代码仓库

### 获取 Notion 凭证

这是最关键的一步，请仔细按照以下步骤操作：

#### 获取 token_v2

1. 登录 [Notion](https://www.notion.so/)
2. 打开浏览器开发者工具 (F12)
3. 切换到 **Application** 标签页
4. 展开 **Cookies** → **https://www.notion.so**
5. 找到 `token_v2` 项，复制其 **Value**

#### 获取 Space ID 和 User ID

1. 在开发者工具中切换到 **Network** 标签页
2. 在 Notion 中进行任意操作（如点击页面）
3. 找到 `getRecordValues` 或类似请求
4. 查看请求头中的：
   - `x-notion-active-user-header` → **User ID**
   - `x-notion-space-id` → **Space ID**

### 配置环境变量

1. **克隆项目**:
   ```bash
   git clone https://github.com/lzA6/notion-2api.git
   cd notion-2api
   ```

2. **创建配置文件**:
   ```bash
   cp .env.example .env
   ```

3. **编辑 `.env` 文件**:
   ```env
   # 安全配置（可选）
   API_MASTER_KEY=your_secret_key_here
   
   # 部署配置，host 网络模式下 nginx 直接监听这个宿主机端口
   NGINX_PORT=4002
   
   # Notion 凭证（必需）
   # 建议填写浏览器请求里的完整 Cookie；只填 token_v2 可能无法访问 Notion AI 接口
   NOTION_COOKIE="你的完整 Cookie"
   NOTION_SPACE_ID="你的Space ID"
   NOTION_SPACE_NAME="你的Space名称"
   NOTION_SPACE_VIEW_ID="runInferenceTranscript context里的spaceViewId，可留空"
   NOTION_USER_ID="你的User ID"
   NOTION_USER_NAME="你的名字"
   NOTION_USER_EMAIL="your_email@example.com"
   NOTION_CLIENT_VERSION="23.13.20260529.0633"
   NOTION_REFERER="https://www.notion.so/"
   # 可选：访问 Notion 上游接口使用的代理
   NOTION_PROXY="127.0.0.1:7890"
   ```

### 启动服务

```bash
docker-compose up -d --build
```

### 测试 API

```bash
curl http://localhost:4002/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your_secret_key_here" \
  -d '{
    "model": "claude-sonnet-4.5",
    "messages": [{"role": "user", "content": "你好，请介绍一下你自己"}],
    "stream": true
  }'
```

如果一切正常，你将看到来自 Notion AI 的流式响应！

---

## 🧠 源码解析

### 核心模块架构

```mermaid
graph TB
    A[main.py] --> B[Config Manager]
    A --> C[Auth Middleware]
    A --> D[Route Handlers]
    D --> E[Notion Provider]
    E --> F[Session Management]
    E --> G[Stream Parser]
    E --> H[Response Formatter]
    G --> I[NDJSON Parser]
    H --> J[SSE Generator]
```

### `main.py` - 应用入口

负责初始化 FastAPI 应用、配置中间件和路由：

```python
@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    auth: bool = Depends(verify_api_key)
):
    """处理聊天补全请求，支持流式和非流式响应"""
```

### `app/core/config.py` - 配置管理

使用 Pydantic 进行类型安全的配置管理：

```python
class Settings(BaseSettings):
    """应用配置模型，自动从环境变量加载"""
    API_MASTER_KEY: str = "1"
    NGINX_PORT: int = 4002
    NOTION_COOKIE: str
    NOTION_SPACE_ID: str
    NOTION_USER_ID: str
```

### `app/providers/notion_provider.py` - 核心逻辑

实现与 Notion AI 的交互：

- **会话预热**: 初始化时访问 Notion 以获取有效 Cookie
- **线程管理**: 为每个对话创建独立的 Notion 线程
- **流式解析**: 实时解析 Notion 的 NDJSON 响应流
- **数据清洗**: 清理响应中的冗余信息和格式标记

### `app/utils/sse_utils.py` - 流式响应

将数据转换为 Server-Sent Events 格式：

```python
def create_sse_data(data: dict) -> str:
    """将数据转换为 SSE 格式"""
    return f"data: {json.dumps(data)}\n\n"
```

---

## 📊 项目评估

### ✅ 已完成功能

- [x] 核心代理功能 - OpenAI API 到 Notion AI 的协议转换
- [x] 完整的流式响应支持
- [x] Docker 容器化部署
- [x] Cloudflare 防护绕过
- [x] 多模型支持（Claude、GPT、Gemini）
- [x] API 密钥认证
- [x] 环境配置管理

### 🌟 核心优势

1. **生态兼容性**: 无缝接入现有 OpenAI 生态工具
2. **成本效益**: 利用现有 Notion 订阅，无需额外付费
3. **隐私保护**: 私有化部署，数据完全自主控制
4. **技术价值**: 学习现代 Web 开发和 API 设计的优秀范例

### ⚠️ 限制与挑战

1. **API 稳定性**: 依赖 Notion 非官方接口，存在变更风险
2. **凭证维护**: Notion Cookie 会过期，需要定期更新；Notion AI 接口通常需要完整 Cookie
3. **防护对抗**: Cloudflare 策略更新可能导致连接失败
4. **功能局限**: 目前主要支持聊天补全功能

---

## 🗺️ 未来规划

### 🎯 短期目标

- [x] 支持非流式响应 (`stream=false`)
- [ ] 增强错误处理和用户反馈
- [ ] 改进 token 自动刷新机制
- [ ] 动态模型列表获取
- [ ] 更详细的运行日志

### 💡 长期愿景

- [ ] 多账户负载均衡
- [ ] Function Calling 支持
- [ ] Web 管理界面
- [ ] 插件化架构
- [ ] 使用量统计和限制

---

## 🤖 AI 开发者指南

### 项目理解要点

1. **架构模式**: 这是一个典型的协议转换代理服务
2. **核心技术**: 异步编程、流式处理、反爬虫技术
3. **关键挑战**: 非官方 API 的稳定性和兼容性维护
4. **扩展方向**: 更多 AI 服务的协议转换支持

### 代码贡献建议

- 优先修复稳定性相关问题
- 改进错误处理和用户反馈
- 添加单元测试和集成测试
- 优化文档和示例代码

---

## 💖 贡献指南

我们欢迎各种形式的贡献：

- 🐛 **问题反馈**: 提交 [Issue](https://github.com/lzA6/notion-2api/issues)
- 🔧 **代码贡献**: 创建 [Pull Request](https://github.com/lzA6/notion-2api/pulls)
- 💡 **想法分享**: 参与 [Discussions](https://github.com/lzA6/notion-2api/discussions)
- ⭐ **项目支持**: 给项目点赞和分享

每一个贡献，无论大小，都是对开源世界的宝贵礼物。

---

**让我们一起，用代码创造更多可能性！** 🚀
