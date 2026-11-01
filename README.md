# PaperPilot — 顶会顶刊论文智能追踪系统

每天自动从 **arXiv / OpenAlex / OpenReview / Crossref / GitHub** 采集你关注的顶会顶刊论文，
按研究方向打分排序，对重点论文做「创新性 / 继承关系 / 技术迁移」深度分析，
并把结果**自动交到 Zotero 管文献、提炼进 Obsidian 建知识库、在 PaperPilot 工作台展示、
导出给 ima 做交互问答**，形成「发现 → 归档 → 提炼 → 决策」的科研闭环。

> 纯本地运行、数据不上传。分析依赖大模型时，只把论文摘要/元数据发给你自己配置的 LLM 接口。

---

## 0. 这是干什么的（30 秒看懂）

- **每天**自动从顶会顶刊抓最新论文（默认：AAAI / NeurIPS / ICML / CVPR / ICCV / ECCV / KDD + TPAMI / TIP / TNNLS）。
- **按你关注的方向**打分排序（默认 6 个主题：生成式模型、多智能体协同、脑机接口、小样本、迁移学习、智能体设计）。
- **对最相关的 N 篇**用大模型做深度分析（创新点、继承自哪些论文、能迁移到哪）。
- **自动归档**：Zotero 存文献原件、Obsidian 生成知识卡、网页工作台统一浏览、ima 导出做问答。

一句话：**你只管看结果，找论文、读摘要、做分析、归档的活它全包了。**

---

## 1. 四个工具的职责边界

| 工具 | 职责 | 是否必须 |
|------|------|---------|
| **PaperPilot**（本项目） | 采集、打分、分析、调度、生成报告 | ✅ 必须 |
| **Zotero** | 文献原件、PDF、批注、引用 | 可选（不装则跳过文献归档）|
| **Obsidian** | 知识卡、方法继承、研究假设 | 可选（不装则只生成 Markdown 报告）|
| **ima** | 自然语言问答、跨论文比较 | 可选（不装则跳过导出）|

> 原则：**论文事实、模型判断、你的研究假设分开存储**；自动生成内容与手写笔记分区隔离，
> 程序重跑不会覆盖你的精读结论。

**没有 Zotero / Obsidian / ima 也能用**：采集、打分、分析、生成 Markdown 日报 + HTML 工作台
这些核心功能完全独立，可选软件未安装/未运行时对应同步自动跳过。

---

## 2. 环境要求

- **Python 3.9+**（推荐 3.11/3.12；本项目在 3.13 上实测通过）
- 联网（访问 arXiv / OpenAlex / Crossref / GitHub）
- 可选：Zotero 7+、Obsidian、ima

> 需要操作系统：Windows / macOS / Linux 均可。文档示例用 Windows 路径，其他系统自行替换。

---

## 3. 快速安装

```bash
# 1. 进入项目目录
cd paperpilot

# 2.（推荐）创建虚拟环境
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 3. 安装依赖
python -m pip install -r requirements.txt

# 4. 配置（关键一步，见下）
copy .env.example .env        # Windows；macOS/Linux 用 cp .env.example .env
# 用文本编辑器打开 .env，填入 LLM_API_KEY（不填也能跑，只是跳过深度分析）
```

### 4. 配置 .env（大模型）

打开 `.env`，只需填一个字段即可启用深度分析：

```ini
LLM_API_KEY=你的密钥
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro
```

- 任意 **OpenAI 兼容**服务商都可以：DeepSeek、OpenAI、通义千问、Moonshot、本地 Ollama 等。
- **不填 `LLM_API_KEY` 也能完整运行**：系统仍会采集、打分、生成报告，只是深度分析显示为「跳过」。

其余可选字段：`GITHUB_TOKEN`（提高 GitHub 搜索配额）、`SMTP_*`/`FEISHU_WEBHOOK_URL`（邮件/飞书推送）。

**邮件日报（可选，推荐）**：晚间点评会生成一份日报（Action1 采集 / Action2 精读 / Action3 点评 / Action4 学习评价），
填写以下 5 项即可自动发到邮箱：

```ini
SMTP_HOST=smtp.qq.com      # 或 smtp.163.com / smtp.gmail.com
SMTP_PORT=465              # 465=SSL，587=STARTTLS
SMTP_USER=你的邮箱地址
SMTP_PASSWORD=你的授权码   # ★授权码，不是登录密码
SMTP_TO=收件邮箱           # 可与 SMTP_USER 相同
```

> 授权码获取：QQ 邮箱「设置 → 账户 → 开启 SMTP 服务」生成授权码；163 同理。
> 配好后把 `config.yaml` 的 `notifications.email_enabled` 改成 `true`。

### 5. 调整 config.yaml（研究方向 + 追踪范围）

`config.yaml` 是你最常改的文件，**改完下次运行即生效**：

- `topics:` —— 关注的研究方向（key 是英文标识，`label` 是中文名，`keywords` 是召回关键词）
- `venues:` —— 追踪的顶会顶刊（含 OpenAlex source id / Crossref ISSN）
- `app.daily_analysis_limit` —— 每天深度分析上限（控制大模型成本，默认 10）
- `app.lookback_days` —— 向前追溯天数（默认 7）
- `app.max_topic_keywords` —— 每个主题关键词数量上限（默认 40，防止自动扩充时膨胀）
- `app.max_keyword_tags` —— 每篇论文关键词标签数量上限（默认 5）
- `app.daily_ingest_limit` —— 每天最多**新增入库**多少篇（默认 20，0=不限）
- `app.ingest_min_relevance` —— 入库相关度阈值（默认 25，低于此值的候选直接丢弃）
- `app.ingest_per_topic_limit` —— 每个主题每天最多入库几篇（默认 4，0=不限）
- `app.ingest_wildcard_limit` —— 顶会机动名额（默认 2，见下方「采集筛选规则」）

**采集筛选规则（四层漏斗）**：每天抓到的候选论文（约 100~200 篇）不会全量入库，
而是依次经过四层筛选，最终受 `daily_ingest_limit` 约束：

```
第①层 相关度阈值   relevance_score < ingest_min_relevance(25) 直接丢弃
第②层 领域归主     命中多主题的论文归入「最高分主题」，只占一个名额
第③层 领域配额     每个主题取相关度 Top ingest_per_topic_limit(4)
第④层 机动名额     被阈值过滤、但属「顶会顶刊 + 零关键词命中」的全新方向论文，优先保底 ingest_wildcard_limit(2) 篇
第⑤层 全局上限     各领域配额之和超 daily_ingest_limit(20) 时，按领域轮转(round-robin)收敛，保证小领域不被大领域挤出
```

> 只对**新论文**做关键词分析（调 LLM）；已在库的论文保留既有关键词，不重复分析。

**⚠️ 改 topics 后要做一件事**：已入库的旧论文不会自动重新归类，跑一次：

```bash
python retag_topics.py --dry-run   # 先看会改变哪些
python retag_topics.py             # 确认后重算（只算命中关键词）
python retag_topics.py --llm       # 重算 + LLM 提炼补齐关键词标签（慢，全库约2.6小时）
python retag_topics.py --sync      # 重算 + 同步 Zotero 归集
```

> **关键词标签**：每篇论文除了主题标签（topics），还有最多 5 个关键词标签。
> 关键词标签优先取「命中的主题关键词」；不足 5 个时用大模型从标题+摘要提炼
> 补充。大模型新提炼、且能归属到某个主题的新关键词，会回写进 config.yaml 对应
> 主题的 `keywords`（全库归一化去重 + 每主题上限），下次采集自动参与召回。
>
> **语义合并去重**：标签和回写前都会做「语义合并」——若一个词与已有词在语义上
> 等价、同义改写、或是已有词的下位词/子方向（如 `source-free domain adaptation`
> 相对 `domain adaptation`），则统一用**已有词**替代，不并存两个；过于碎片化的
> 修饰残词会被丢弃。字面层（完全相等/互为子串）先本地判断，字面判不了的再交给
> 大模型做语义判断。

> ⚠️ YAML 格式注意：冒号必须用**英文半角 `:`**，且冒号后跟一个空格。
> 写 `weight: 1.0`（对），不要写 `weight：1.0`（中文全角冒号，会导致解析失败）。

**🎯 更省事的方式：直接在 Zotero 里增删主题集合，让程序反向同步回 config.yaml**（无需手改 YAML）：

```bash
python sync_topics_from_zotero.py --dry-run   # 先看会新增/删除哪些主题
python sync_topics_from_zotero.py             # 交互确认后应用
python sync_topics_from_zotero.py --yes       # 直接应用
```

你在 Zotero 的 `PaperPilot` 父集合下**新建一个子集合**（比如「强化学习」），
程序会自动：①在 config.yaml 里补全这个 topic 的英文 key 和关键词（用大模型
生成，如「强化学习」→ `reinforcement_learning` + 十几条检索关键词）；②**关键词
去重**——若新主题的某个关键词之前已作为其它旧主题的子关键词存在（例如
`reinforcement learning` 原本是「机器学习」下的关键词），会自动从那些旧主题里
删除，避免同一词既是独立主题又是另一个主题的子词导致重复召回；③重算全库标签；
④建好 Obsidian 主题目录；⑤归集 Zotero 集合。**删除** Zotero 集合则反向删除
对应 topic（删除前会提醒确认）。

> 映射约定：Zotero 集合名 == config.yaml 里 topic 的 `label`（中文名）。
> `00_Inbox` / `10_ToRead` / `20_Analyzed` 是状态集合，不参与主题同步。

---

## 6. 运行

### 一键操作面板（推荐）

工作台内置「一键操作」面板，点击按钮即可在浏览器里执行本项目所有脚本，日志实时显示。

```bash
python paperpilot_server.py        # 启动本地控制服务，自动打开浏览器
```

浏览器打开后，工作台顶部会出现操作面板，包含这些按钮：

| 按钮 | 对应脚本 | 作用 |
|------|---------|------|
| 全量采集 | `main.py` | 采集新论文 + 深度分析 + 生成报告 |
| 单篇入库·列出待处理 | `ingest_one.py --list` | 列出 Zotero 里待入库的条目 |
| 单篇入库·精读 | `ingest_one.py` | 临时论文入库 + 深度精读（可填标题关键词） |
| 重算标签·预览 / 执行 | `retag_topics.py` | 重算全库主题/关键词标签 |
| Zotero 反向同步·预览 / 执行 | `sync_zotero_back.py` | Zotero 新条目补录成文献卡 |
| 主题反向同步·预览 / 执行 | `sync_topics_from_zotero.py` | Zotero 集合反向更新 config.yaml |
| 生成工作台 | `_build_workbench.py` | 从数据库重新生成工作台 HTML |
| 晚间点评 | `evening.py` | 读精读笔记生成点评 |

> 服务启动参数：`--port 8900` 换端口、`--no-browser` 不自动开浏览器。
> 需要执行操作的脚本（采集、精读、重算等）仍需 Zotero 客户端在线（反向同步类）。

### 手动跑一次

```bash
python main.py
```

跑完后打开网页工作台：

```bash
start paperpilot_workbench.html     # Windows
# macOS: open paperpilot_workbench.html
# Linux: xdg-open paperpilot_workbench.html
```

### 每天自动跑（三段式自动化）

系统把每天的任务拆成三个时段，形成「**采集 → 精读 → 点评 + 日报邮件**」闭环：

| 时间 | 动作 | 命令 |
|------|------|------|
| **00:00** 采集入库 | 查各会议/期刊更新，爬取→去重→四层筛选→入库 Zotero | `python main.py --collect-only` |
| **07:00** 按主题精读 | 每个主题选一篇「未精读中优先级最高」的论文精读，写 Obsidian 文献卡 | `python morning_read.py` |
| **23:00** 点评+日报 | 读当天新增「我的判断」笔记并点评，生成日报并发邮件 | `python night_review.py` |

**方式一（推荐）：Windows 任务计划程序**（关机也能按计划触发）

```bat
schtasks /Create /SC DAILY /ST 00:00 /TN PaperPilotCollect ^
  /TR "python C:\路径\paperpilot\main.py --collect-only"
schtasks /Create /SC DAILY /ST 07:00 /TN PaperPilotRead ^
  /TR "python C:\路径\paperpilot\morning_read.py"
schtasks /Create /SC DAILY /ST 23:00 /TN PaperPilotReview ^
  /TR "python C:\路径\paperpilot\night_review.py"
```

**方式二：进程内定时器**（需保持程序常开）

```bash
python scheduler.py    # 自动按 config.yaml 的 schedule 三时段执行
```

> 三个时段的时间可在 `config.yaml` 的 `schedule` 节调整（`collect_hour` / `read_hour` / `review_hour`）。
> 每个任务跑完都会自动重新生成工作台，保证工作台信息始终最新。
> 日报格式：`#日报：日期` + `Action1 论文库更新` + `Action2 精读列表` + `Action3 点评情况` + `Action4 学习评价`，存到 `data/reports/evening_daily_*.md` 并邮件推送。

### 临时单篇入库 + 精读（不用等每日同步）

临时找到一篇论文，拖进 Zotero 后，想立刻对**这一篇**做入库、打标签、深度分析：

```bash
python ingest_one.py --list                      # 列出 Zotero 里待入库的条目
python ingest_one.py --title "domain adaptation" # 按标题关键词匹配并处理这一篇
python ingest_one.py                             # 交互选择一篇
python ingest_one.py --all                       # 处理所有待入库条目
python ingest_one.py --title "..." --depth deep  # 指定分析深度 skim/standard/deep
```

对选中的论文依次做：补录数据库（生成 citekey + 主题标签 + 关键词标签）→ 三遍阅读式
深度分析 → 生成单篇报告（`data/reports/papers/`）→ 写 Obsidian 文献卡 → 归集 Zotero
状态分区与主题集合。**前置**：Zotero 客户端打开、已授权通信（同第 8 节）。

---

## 7. 运行产物

成功后生成：

| 产物 | 路径 | 说明 |
|------|------|------|
| 论文库 | `data/papers.db` | SQLite，含全部论文 + 分析结果 |
| BibTeX 导入文件 | `data/exports/paperpilot_import.bib` | 手动导入 Zotero 用（含稳定引用键）|
| 每日简报 | `data/reports/daily_YYYY-MM-DD.md` | 当天采集 + 分析摘要 |
| 深度分析 | `data/reports/papers/*.md` | 重点论文的逐篇分析 |
| 网页工作台 | `paperpilot_workbench.html` | 搜索 / 按主题·会议·关键词·分析·点评状态筛选 / 继续精读 / 一键跳转 |
| Obsidian 知识库 | 你配置的 `vault_path`（默认 `C:/Users/pl/ResearchVault`）| 文献卡 / 日报 / 模板 |
| ima 导出 | `data/ima_exports/` | 精选整合 + 科研指令模板 |

---

## 8. 与 Zotero / Obsidian / ima 联动

### Zotero（文献原件）

- 自动入库**无需 token**：Zotero「设置 → 高级」勾选「允许其他应用程序与本机 Zotero 通信」，
  程序运行时会弹授权框，点「Always Allow」即可（授权自动缓存到 `data/zotero_local_key.json`）。
- Zotero 未开启时，自动生成 `data/exports/paperpilot_import.bib`，手动「文件 → 导入」即可。

### Obsidian（知识库）

- 自动创建 Vault 目录结构，把重点论文写成文献卡（`01-Literature/<citekey>.md`）。
- `%% PAPERPILOT_AUTO %%` 区块会被程序更新；`%% PAPERPILOT_USER %%` 区块（你的手写笔记）永不覆盖。
- 5 套模板在 `99-Templates/`，方法卡/研究卡/实验卡/综合分析/IMA 导出里的「论文来源」既可用
  `[[citekey]]` 挂文献卡，也可用 `[标题](zotero://select/items/KEY)` 跳转 Zotero 原件。

### ima（交互问答）

- 精选论文生成整合 Markdown + 科研指令模板到 `data/ima_exports/`，人工导入 ima 知识库后按提示词问答。

### 反向同步（重要：手动加论文的场景）

你在 Zotero 里**手动拖进一篇新论文**，想让知识卡能挂接它时：

```bash
python sync_zotero_back.py --dry-run   # 先看会补录哪些
python sync_zotero_back.py --yes       # 确认后补录成文献卡
```

补录后这篇论文就有了自己的 `[[citekey]]` 文献卡，所有知识卡都能双向链接它。

---

## 9. 数据源与核验策略

| 信源 | 作用 | 说明 |
|------|------|------|
| arXiv | 最新预印本发现 | 按主题关键词 + 分类查询；从 comment/journal-ref 识别录用 |
| OpenAlex | 会议/期刊核验 | 按 source id 拉最新论文，补 DOI/引用 |
| OpenReview | 顶会投稿（最早） | NeurIPS/ICML/ICLR 官方投稿平台，按 venueid 拉录用论文 |
| Crossref | 期刊论文 | 按 ISSN 拉 TPAMI/TIP/TNNLS 最新 |
| GitHub | 代码关联 | 按主题搜仓库，描述含 arXiv 链接的自动挂论文（候选未核验）|

> OpenAlex 对 ICML/ECCV 收录不全，这两类由 arXiv 分类 + 主题词兜底；
> OpenReview 覆盖 NeurIPS/ICML/ICLR（CVPR/ICCV/AAAI/KDD/ECCV 不在其上，由 arXiv/OpenAlex 覆盖）；
> GitHub 关联为「候选未核验」，请人工确认。

---

## 10. 跨电脑部署 + 交给 WorkBuddy（AI 助手）使用

把整套程序发给别人（Git 拉取或拷贝目录）后，对方在自己电脑上部署一套，只需三步：

### 10.1 部署三步走

**第一步：创建自己的 `.env`（关键，勿跳过）**

项目里只有 `.env.example`（不含任何密钥），**没有 `.env`**。拉取后必须复制一份并填写：

```bash
copy .env.example .env        # Windows；macOS/Linux 用 cp .env.example .env
```

然后打开 `.env`，至少填 `LLM_API_KEY`（深度分析必需）；要收日报邮件再填 `SMTP_*` 五项（见第 4 节）。
`.env` 已在 `.gitignore` 中，**不会也不应提交到 Git**，每台机器各自维护一份。

**第二步：安装依赖**

```bash
python -m venv .venv && .venv\Scripts\activate      # macOS/Linux 用 source .venv/bin/activate
python -m pip install -r requirements.txt
```

**第三步：改路径并首跑**

- 打开 `config.yaml`，把 `obsidian.vault_path` 改成**你自己的 Obsidian Vault 路径**（不用 Obsidian 就把 `obsidian.enabled` 设 `false`）；
- 跑一次 `python main.py` 验证。

### 10.2 交给 WorkBuddy 操作

对方若用 WorkBuddy，把项目目录作为工作目录打开后，直接说自然语言即可：

| 想做的事 | 对 WorkBuddy 说 |
|---------|----------------|
| 首次部署 | 「帮我装依赖、配好 .env、跑一遍 main.py」 |
| 跑一次采集分析 | 「运行 main.py」 |
| 部署每天自动化 | 「帮我配置三段式定时任务（采集/精读/点评）」 |
| 改研究方向 | 「帮我在 config.yaml 里加一个关于 XXX 的主题」 |
| 加主题后刷新旧论文 | 「跑 retag_topics.py」 |
| 手动加了 Zotero 论文 | 「跑 sync_zotero_back.py」 |
| 在 Zotero 增删主题集合 | 「跑 sync_topics_from_zotero.py」 |
| 出问题了排查 | 「main.py 报错了，帮我看看」 |
| 生成使用手册 | 「写一份安装使用手册」 |

WorkBuddy 会自动识别项目、用正确的 Python 解释器跑起来、检查产出、汇报结果。

> 注意：`config.yaml` 里 `obsidian.vault_path` 默认是原作者机器的路径，
> 务必改成对方自己的路径（或让 WorkBuddy 帮忙改）。

---

## 11. 项目结构

```
paperpilot/
├── config.yaml            # 主配置（研究方向 / 追踪范围 / 路径）
├── schema.sql             # SQLite 表结构
├── main.py                # 采集→去重→评分→分析→报告→同步（主入口）
├── paperpilot_server.py   # 本地控制服务（工作台「一键操作」后端）
├── _build_workbench.py    # 从数据库重新生成工作台 HTML
├── scheduler.py           # 三段式定时器（00:00 采集 / 07:00 精读 / 23:00 点评）
├── morning_read.py        # 早间精读：每主题选一篇未精读论文精读并写 Obsidian
├── night_review.py        # 晚间点评 + 日报 + 邮件推送
├── evening.py             # 晚间点评（旧版，读 Obsidian 笔记生成反馈）
├── retag_topics.py        # 改主题后重算旧论文标签
├── ingest_one.py          # 临时单篇论文入库 + 深度精读
├── sync_zotero_back.py    # Zotero → PaperPilot 反向同步（论文）
├── sync_topics_from_zotero.py  # Zotero 集合 → config.yaml 反向同步（主题）
├── organize_zotero.py     # Zotero 集合整理
├── reset_zotero.py        # Zotero 全量重置（激进，慎用）
├── reset_zotero_gentle.py # Zotero 温和清理（分批+断线重连，推荐）
├── paperpilot/
│   ├── config.py / db.py / model.py / topics.py / ranker.py
│   ├── daily_state.py     # 三段式任务间共享的每日状态（采集/精读/点评结果）
│   ├── analyzer.py        # LLM 深度分析（v3：三遍阅读法 + Five Cs + 分级）
│   ├── analysis_level.py  # 分析深度/证据等级分级
│   ├── schemas.py         # 分析结果结构校验
│   ├── related.py         # 相关文献检索（OpenAlex）
│   ├── feedback.py        # 晚间点评 + 分析质量审查
│   ├── citekey.py         # 稳定引用键
│   ├── zotero.py / obsidian.py / ima.py / reports.py / notify.py
│   └── collectors/        # arxiv / openalex / openreview / crossref / github
├── data/                  # papers.db / exports / reports / ima_exports
└── logs/
```

---

## 12. 已知限制

- 深度分析基于**摘要**（未解析 PDF 全文），重要结论应回到原文核验。
- GitHub 仓库尚未做「官方实现 / 第三方复现」核验。
- 论文关系为候选，尚未构建完整的引用/继承图谱。
- Zotero 自动入库需 Zotero 7+ 本地 API 开启；ima 暂以人工导入为主。

---

## 13. 常见问题（FAQ）

**Q：不填 LLM_API_KEY 能用吗？**
能。采集、打分、报告、归档全部正常，只是深度分析标记为「跳过」。

**Q：Zotero 里没有自动出现论文？**
检查：① Zotero 是否开启；②「设置 → 高级 → 允许其他应用通信」是否勾选；③ 运行时是否点了「Always Allow」。若都正常但仍无，手动导入 `data/exports/paperpilot_import.bib`。

**Q：改了 topics 后，旧论文没变化？**
这是预期行为——需要跑 `python retag_topics.py` 重算（见第 5 节）。

**Q：为什么 arXiv 老是 502 / 超时？**
arXiv 服务端偶尔不稳，属于外部问题。系统会自动 fallback 到 OpenAlex 预印本兜底，流程仍能走通。

**Q：Zotero 里出现重复条目怎么办？**
用 `python reset_zotero_gentle.py` 温和清理（分批+断线重连，比 `reset_zotero.py` 稳）。

**Q：日报邮件收不到？**
逐项检查：① `.env` 的 SMTP 五项是否填全；② `config.yaml` 的 `notifications.email_enabled` 是否为 `true`；③ `SMTP_PASSWORD` 填的是否是「授权码」而非登录密码；④ 邮箱是否开启 SMTP 服务。
