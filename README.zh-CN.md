# local-agent-memory

> [English](./README.md) | **简体中文**

**一套本地优先、以 Markdown + Git 为唯一真源、通过本机只读 MCP 跨多端 AI Agent（Antigravity / Codex / Hermes 等）共享的个人全局记忆架构——零云端绑定、内置中文 FTS 召回补丁、防漂移静态核心分发与 G0–G9 红队门禁。**

---

## 为什么做这套架构

现在主流 AI 工具基本都自带记忆（`MEMORY.md`、内置 SQLite 或云端画像）。但只要你同时在用多个 Agent——比如 Antigravity 做架构规划、Codex 写代码、Hermes 多 Profile 跑日常工作流——很快就会撞上 4 个真实痛点：

1. **多端真相分裂**：每个客户端各自演化出一套互不同步的过期偏好和项目边界。
2. **静默污染记忆库**：直接开放 `write_note` / `edit_note` 给 Agent，几次会话后长期规则就会被一次性噪音写脏。
3. **中文自然语言 FTS 几乎失效**：向量离线或降级时，上游默认 FTS5 分词把无空格中文问句拼成整段精确短语，纯关键词召回率往往跌到个位数。
4. **跨域隐私串味**：纯向量相似度会把私密健康/个人日程卡误召回到完全无关的写代码或公开发文任务里。

`local-agent-memory` 用硬契约解决这 4 个问题：**Markdown + Git 永远是唯一真源；SQLite 与向量库只是可删可重建的派生缓存；MCP 服务端默认只读；Agent 只能通过文件工具向 `pending/` 追加 YAML 补丁，经人工审批后才进入正式卡片。**

---

## 总体架构

```mermaid
flowchart LR
    subgraph SOT["唯一真源（Markdown + Git）"]
        CORE["core/core-profile.md<br/>（.bmignore 排除）"]
        DOM["domains/&lt;domain&gt;/*.md<br/>（原子规则卡：L0/L1/L2）"]
        EP["episodes/YYYY-MM/*.md<br/>（一次性排障/实操，非永久规则）"]
        PEND["pending/&lt;agent&gt;/YYYY-MM-DD.md<br/>（B2 暂存审批区，.bmignore）"]
        ARCH["archive/<br/>（物理移出清索引，.bmignore）"]
    end

    REST["../Restricted/<br/>（物理位于 MCP 索引根之外）"]

    subgraph LOCAL["本机回环双常驻服务（127.0.0.1）"]
        OLL["Ollama bge-m3<br/>（1024 维，127.0.0.1:11434）"]
        BM["basic-memory==1.0.0<br/>补丁版：服务端只读 + 中文 FTS<br/>（Streamable HTTP :8765/mcp）"]
    end

    subgraph CLIENTS["多端客户端"]
        AG["Antigravity"]
        CX["Codex"]
        HM["Hermes MCP Profiles"]
        HN["Hermes 无 MCP Profile<br/>（文件直读 _index.md）"]
    end

    CORE -->|"sync-static-core.ps1<br/>（SHA-256 漂移拦截）"| AG & CX & HM & HN
    DOM & EP --> BM
    OLL <--> BM
    BM -->|"只读 MCP<br/>（bm_search / bm_read）"| AG & CX & HM
    AG & CX & HM & HN -.->|"仅文件追加<br/>（满足 4 门槛才可写）"| PEND
    DOM -.->|"兜底直读"| HN
    REST -.-x|"0 索引 / 0 向量"| BM
```

---

## 核心工程特性

- **派生缓存可删可重建，Git 100% 干净**：`memory.db`（SQLite FTS5 + `sqlite-vec`）随时可删库重建；`ensure_frontmatter_on_sync=false` 保证索引过程绝不回写弄脏 `git status`（**G2**）。
- **面向 `basic-memory==1.0.0` 的幂等三合一补丁**（[`scripts/apply-zh-fts-patch.py`](scripts/apply-zh-fts-patch.py)）：
  1. 补齐读取项目根目录 `<vault>/.bmignore`（原版仅读全局配置目录 `.bmignore`）。
  2. 为中文自然语言查询补齐 Bigram + BM25 OR 松弛层，内置停用字/停用双字词过滤与多词覆盖门槛（在 20 条冻结中文测试集上将纯 FTS Top-1 召回从 `1/20` 提升至 `20/20`，同时保持 `5/5` 负样本零幻觉）。
  3. 通过 FastMCP `Visibility(False)` 在服务端默认彻底隐藏 `bm_write`/`bm_edit`/`bm_move`/`bm_delete`/`bm_project_delete`，并拦截 `bm_project(command="create")`（仅显式设置 `BASIC_MEMORY_READ_ONLY=false` 才放开）。
- **两级无感降级与自动自愈（G9）**：本机 Ollama 离线导致 `bm_search(mode="hybrid")` 报错时，客户端优先降级重试 `bm_search(mode="text")`（依托补丁版中文 FTS）；仅当 MCP 本身不可用或结果为空时再直读域 `_index.md`。Ollama 恢复后 `basic-memory` 无需重启即可自动自愈。
- **托管区块单向分发与防漂移**（[`scripts/sync-static-core.ps1`](scripts/sync-static-core.ps1)）：
  - 默认 `dry-run`（加 `-Apply` 才写盘）；
  - 支持按端条件渲染（`<!-- target: ... -->`）与 `<= 2 KB` 的 `core-lite` 精简块提取；
  - 保护 Antigravity 首 3 行 `trigger: always_on`、首次注入自动生成带时间戳 `.bak`、写入前校验 SHA-256 防本地篡改覆盖（漂移即输出逐行 diff 并中止）、强制 UTF-8 无 BOM + LF、硬拦截严禁修改 `USER.md` / `MEMORY.md`。
- **默认纯只读巡检脚本**（[`scripts/audit-freshness.py`](scripts/audit-freshness.py)）：
  - 自动发现 `domains/` 下所有域，校验 Frontmatter 契约、`permalink` 前缀编码（`<domain>-*` vs `episode-YYYYMMDD-*`）、`L0/L1/L2` TTL 超期、相对 Markdown 链接断链、UTF-8/LF 编码、`domains/` 未授权未提交改动及 `pending/` 14 天告警 / 30 天归置。
  - 显式传 `--write-digest` 生成每周人工审批清单 `pending/_digest.md`。
- **Windows 11 无黑框保活套件**（[`scripts/windows/`](scripts/windows/)）：
  - 基于 `pythonw.exe` 启动（自带 `sys.stdin=os.devnull` 补丁修复 GUI 子系统下 `isatty()` 崩溃）、强制 `HF_HUB_OFFLINE=1` 与 `NO_PROXY=127.0.0.1,localhost`，由任务计划程序在当前用户登录时拉起并支持失败 1 分钟自动重启。
- **G0–G9 门禁评测与失败退路矩阵**（[`docs/evaluation-gates.md`](docs/evaluation-gates.md)）：
  - 先在独立副本跑通冻结正/负/红队样本与 30 分钟耐力压测，再切入真实工作流。

---

## 5 分钟快速体验（内置虚构 Demo 库）

仓库自带一套 **100% 虚构**的示例记忆库（`examples/demo-vault/`），Clone 下来即可零侵入跑通核心脚本：

### 1. 运行只读新鲜度与治理体检
```bash
python scripts/audit-freshness.py --root examples/demo-vault --strict
```

### 2. 预览多端静态核心分发（默认 Dry-Run，不写入任何文件）
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync-static-core.ps1 `
  -CorePath examples/demo-vault/core/core-profile.md `
  -ManifestPath scripts/sync-targets.example.json
```

### 3. 搭建你自己的本地多端记忆层
按 [`docs/client-setup.md`](docs/client-setup.md) 依次执行：
1. 拉取本地向量模型：`ollama pull bge-m3`（1024 维）；
2. 安装钉死版本：`uv tool install --python 3.14 basic-memory==1.0.0`；
3. 施加幂等补丁：`python scripts/apply-zh-fts-patch.py --apply`；
4. 建库索引并接入 Antigravity / Codex / Hermes；
5. 对照 [`docs/evaluation-gates.md`](docs/evaluation-gates.md) 跑通 G0–G9 门禁。

---

## 仓库目录结构

```text
local-agent-memory/
├── docs/
│   ├── architecture.md               # 四层模型、Frontmatter 契约、TTL、B2 pending/、物理隔离
│   ├── evaluation-gates.md           # G0-G9 门禁矩阵、失败退路与红队评测法
│   └── client-setup.md               # Antigravity / Codex / Hermes 接入与保活指南
├── scripts/
│   ├── apply-zh-fts-patch.py         # basic-memory==1.0.0 三合一幂等补丁
│   ├── audit-freshness.py            # 只读巡检与 pending/_digest.md 生成脚本
│   ├── sync-static-core.ps1          # 托管区块分发与防漂移同步脚本
│   ├── sync-targets.example.json     # 多端目标清单示例
│   └── windows/
│       ├── run_mcp_server.py         # pythonw.exe 无黑框启动器
│       └── register-task-scheduler.ps1
├── templates/                        # basic-memory / Antigravity / Codex / Hermes 配置片段
└── examples/
    ├── demo-vault/                   # 虚构角色（Alex Chen）可运行示例库
    └── demo-restricted/              # 索引根外物理隔离区示例
```

---

## 实测基线（Windows 11 + RTX 5060 Laptop + `bge-m3`）

在 15 篇实测卡片副本上跑完 G0–G9 的实测结果：
- **G3 中文召回（20 条冻结问句）**：Hybrid 混合检索 Top-1 `20/20`，补丁后纯 FTS Top-1 `20/20`（未打补丁前仅 `1/20`）；5 条库外负样本 `5/5` 正确拒答。
- **G4 物理隔离与红队**：隔离/归档/核心/`_index.md` 均 `0` 命中；2 轮 20 次非私密上下文压力测试 `0/20` 误用与泄密。
- **G5 30 分钟并发耐力测（3 端 HTTP 客户端 + 并发写 `pending/`）**：`1,409` 次 MCP 调用 + `353` 次文件追加，`0` SQLite 锁错误、`0` 内存/句柄/TCP `CLOSE_WAIT`/WAL 泄漏，正常延迟 `p50 = 85.2 ms`、`p95 = 156.6 ms`。
- **G6 完全离线**：`HF_HUB_OFFLINE=1` 下 `0` 联网下载，未生成默认 `fastembed_cache`。

---

## 平台说明与第三方许可声明

- **平台支持**：主验证环境为 **Windows 11 原生**。Python 运维脚本（`apply-zh-fts-patch.py`、`audit-freshness.py`）已做跨平台路径兼容（Windows / macOS / Linux）；macOS/Linux 用户可用 `launchd` / `systemd --user` 保活 `basic-memory`，并通过 PowerShell 7 (`pwsh`) 运行 `sync-static-core.ps1`。
- **许可证说明**：
  - 本仓库内的原创文档、模板、示例库与脚本采用 **MIT License**（见 [LICENSE](./LICENSE)）。
  - `basic-memory` 为用户自行安装的独立第三方运行依赖，其上游许可证为 **AGPL-3.0-or-later**；本仓库**不打包也不再分发**任何 `basic-memory` 源码。
  - `Ollama` 与 `bge-m3` 均采用 **MIT License**。
