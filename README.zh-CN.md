[English](README.md) | [中文](README.zh-CN.md)

# TokenFloatS

一个置顶浮窗，同时显示六个 AI 编程工具的实时 Token 用量：**Hermes、opencode、Cline、DeepSeek Harness、Codex、Claude Code**。

只读各工具的本地数据文件，**不发起任何网络请求**。查看用量不消耗 Token，也不会拖慢被监测的工具。

![折叠条](docs/bar.png)

*折叠后的常驻窄条：`THIS RUN` 与 `TOTAL` 两个数字*

![展开面板](docs/panel.png)

*展开后的完整面板：每个工具一行，含用量分布*

![运行记录](docs/runs.png)

*运行记录：每次启动一根柱子，下方是记录明细。横向滚动图表可以查看更早的记录*

---

## 两个数字

| | 含义 |
|---|---|
| **`THIS RUN`** | 本次打开以来消耗的 Token。它是各工具当前累计值与启动时累计值之差，只统计新增用量，每次启动从 0 开始。有一个例外，见下方[「本次运行的统计口径」](#本次运行的统计口径)。 |
| **`TOTAL`** | 各工具自身的历史总量，直接读自它们的数据文件。 |

只有**你确实装了**的工具才会出现在面板里：装了两个就显示两行，不会出现几行红色报错。已安装但读取失败（结构变更、数据库损坏）仍会显示，因为那才是真正需要暴露的问题。

缓存读取通常约占总量的 80% 左右。每个工具下方有一条分布条，把它拆成 input、output、reasoning、cache read、cache write。

### 五个桶的含义

| 桶 | 含义 |
|---|---|
| `input` | 未命中缓存、按原价计费的输入 Token |
| `output` | 生成的 Token |
| `reasoning` | 工具单独上报的思考 Token |
| `cache_read` | 从提示缓存读回的输入 Token（便宜） |
| `cache_write` | 写入缓存的 Token |

**fresh** = `input + output + reasoning`，即不含缓存读取的部分，也就是真正对应花销的那部分。`--once` 会同时输出它与总量。

---

## 安装

需要一个 Python 3.10+。

```bash
git clone https://github.com/EphoReal/TokenFloatS
cd tokenfloats
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

## 运行

双击 `TokenFloatS.cmd`，不会出现黑窗口。

面板默认展开；点右上角的收缩按钮可折叠成上面的窄条，随时可以再展开。两种状态下都能拖动窗口，`Esc` 收起。

托盘图标右键：**显示面板 / 刷新 / 退出**。点关闭按钮只隐藏面板，不退出进程。

重复启动不会开出第二个实例，而是把已有面板唤到前台。

把 `TokenFloatS.cmd` 的快捷方式放进 `shell:startup` 即可开机自启。

### 命令行

```bash
python tokenfloats.py                 # 启动托盘应用
python tokenfloats.py --once          # 打印一次快照后退出
python tokenfloats.py --detect        # 列出探测到的各来源路径
python tokenfloats.py --interval 3    # 本次以 3 秒间隔轮询
```

---

## 读取频率

默认每 **10 秒**重新读取一次，面板底部会显示最后一次刷新的时间。

因为追加型日志改为增量读取（见下方[「说明」](#说明)），一次轮询很便宜：缓存预热后约 **10 ms CPU**，默认间隔下约占单核 0.1%，即使用 2 秒下限也只占 0.4%。在装齐六个工具的机器上实测：

| 间隔 | 每次轮询 CPU | 占单核 |
|---|---|---|
| 2 秒（下限） | 8.6 ms | 0.42 % |
| 5 秒 | 11.7 ms | 0.23 % |
| 10 秒（默认） | 10.4 ms | 0.10 % |

可在 `config.json` 里设置，或用 `--interval 秒数` 只对本次生效（优先级更高）。

---

## 支持的工具

| 工具 | 读取位置 |
|---|---|
| Hermes | `%LOCALAPPDATA%\hermes\state.db`，表 `session_model_usage` |
| opencode | `~/.local/share/opencode/opencode.db`，表 `session_v2` |
| Cline | `~/.cline/data/sessions/*/*.json`，字段 `metadata.aggregateUsage` |
| DeepSeek Harness | `~/.dsh/storages/session_projcache/sessions/*.json`，字段 `rows.tokenUsage.val.totals` |
| Codex | `~/.codex/sessions/**/*.jsonl`，字段 `info.total_token_usage` |
| Claude Code | `~/.claude/projects/**/*.jsonl`，字段 `message.usage` |

仅覆盖上表列出的六个工具，不会自动探测、推断或适配其他 harness。

每个工具可能装在不止一个位置，所以每个源都有一组候选路径按顺序尝试，第一个存在的胜出。`config.json` 里的显式配置永远优先。

想确认某台机器上探测到了什么：

```bash
venv\Scripts\python.exe tokenfloats.py --detect
```

`THIS RUN` 旁的会话数是**真正产生了用量的会话数**，与本次运行数字取自同一批会话，两者永远描述同一集合。例如某个工具磁盘上有 55 个会话文件、其中 12 个从未产生消耗，面板显示的会是 43。

DeepSeek Harness 在目录**上一级** `~/.dsh/storages/session_projcache.json` 还放了一份聚合汇总。这份文件被刻意**不读取**：它只是部分索引——在开发这台机器上它只含 55 个会话中的 28 个，而且其中 7 个的数值比逐会话文件更旧。逐会话文件按 ID 是它的超集，且每轮都会重写，因此只有逐会话文件是权威来源。

---

## 配置

可选。把 `config.json` 放在 `tokenfloats.py` 同目录即可，`config.example.json` 可作为起手模板。文件缺失或格式错误都会静默回退到默认值，不会抛异常。

```json
{
  "poll_seconds": 5,
  "paths": {
    "codex": "D:/somewhere/.codex/sessions",
    "DeepSeek Harness": "E:/dsh/storages/session_projcache"
  }
}
```

- `poll_seconds`：读取间隔。默认 **10**，下限 **2**（低于此值，每轮 stat 并遍历全部存储的开销就不划算了）。`--interval` 会覆盖它。
- `paths`：各来源的路径覆盖。键既可用短名（`hermes`、`opencode`、`cline`、`dsh`、`codex`、`claude_code`），也可用展示名（`Hermes`、`DeepSeek Harness`、`Claude Code` 等）。支持 `~`、`%VAR%`、`$VAR` 展开。

`config.local.json` 不会被 git 跟踪，适合放机器相关的覆盖配置。

---

## 说明

- **只读**：所有数据库都以 `sqlite3.connect("file:...?mode=ro", uri=True)` 打开，绝不创建文件、不阻塞写入方。
- **单实例**：靠操作系统文件锁实现，强杀进程不会留下需要手动清理的残留。
- **可追溯**：每次启动会向 `state/runs.json` 追加一条带日期和时分的记录，含起止时间与各工具数字，最多保留最近 400 次。
- **诊断**：启动无控制台窗口，因此报错写入脚本同目录的 `tokenfloats.log`。
- **增量读取**：Codex 与 Claude Code 每个会话一个持续增长的日志文件，单个 Codex rollout 日志可达数十 MiB。在开发这台机器上，46 个 rollout 文件共 135.6 MiB，而其中带 Token 数据的行只有 3.6 MiB；每轮从头重读要花掉约 500 ms 轮询中的 230 ms，而且是**两遍**（总量一遍、会话快照一遍），因为两者各走了一遍同样的文件。现在每个文件的结果按 `(mtime, size)` 缓存，Codex 日志从上次读完的位置继续读，并保留见过的最大累计记录。未变化的文件根本不读——热轮询因此从约 500 ms 降到约 48 ms。
- **兼容性**：托盘面板仅支持 Windows（依赖 `pystray` 与 `pywin32`）；采集层是纯标准库，在 macOS / Linux 上也能运行。

---

## 运行记录

每次启动都会被记录，无论是正常退出还是中途关闭，所以记录不会漏掉任何一次。
每条记录保存起止时间、本次消耗，以及消耗来自哪些工具，存放在应用旁的
`state/runs.json`，上限为最近 400 条。

打开面板底部的运行记录，六个来源行会让位，换成一张柱状图和一份记录表格。
每根柱子代表一次启动，高度是它消耗的 Token 数，用的是面板中代表输入的同一种亮蓝；
带虚线边缘的是仍在进行中的本次运行。记录打开期间面板尺寸不变，屏幕上的任何内容都不会挪动。

被强杀、崩溃或断电留下未闭合的记录，会在下次启动时被补上结束时间并标记为 `unclean`，也就是说这个结束时间是「下次打开的时刻」，而不是编造出来的。

### 本次运行的统计口径

`THIS RUN` 的算法是：以首次轮询抓取的基线，对每个会话的累计值做差分，并取高水位，因此重复轮询不会重复计数。

**不在启动基线里的会话会按其当前总量整体计入**，因为「基线里没有」与「这是一个全新会话」无法区分：

- 启动后新建的会话从 0 开始，它的总量**就是**它的增长；
- **接续的历史会话**出现时就带着此前的累计值，于是那部分历史会被算进本次运行。

第二种情况是刻意的取舍。各工具的会话列表本身只是一份部分缓存，接续一个它已不再列出的历史会话是常态，而旧做法会静默丢掉这整段用量。现在的代价是可见的：第一次看到该会话的那一轮会报出一个偏大的数字。

---

## 测试

```bash
venv\Scripts\python.exe test_collectors.py       # 不需要 pytest
venv\Scripts\python.exe -m pytest test_collectors.py
```

测试在临时目录里自建夹具，绝不会碰你的真实存储，并通过 `TOKENFLOATS_STATE` 重定向运行记录，因此跑测试不会影响你的历史。

---

## 排障

**面板不出现。** 看脚本同目录的 `tokenfloats.log`：每次启动、每个探测到的来源、任何未捕获异常都会写进去，因为 `pythonw` 没有 stderr 可打印。若出现 `startup-error.log`，说明启动本身就失败了，通常是缺依赖（执行上面的安装步骤）。

**某个来源没有数字。** 运行 `python tokenfloats.py --detect` 会打印每个来源实际解析到的路径。若该工具的数据在非常规位置，用 `config.json` 覆盖。

**数字看起来没更新。** 面板每个轮询周期刷新一次，底部会显示最后一次刷新时间。也可以在托盘菜单点 **刷新** 强制读取一次。

## 许可证

MIT，见 [LICENSE](LICENSE)。

## 作者

- Xiaohongshu / Rednote：@Epho
- GitHub：https://github.com/EphoReal
