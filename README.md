# cb-low-price-daily

这是一个 Codex Skill，用于按照已经完成历史回测的统一口径，每天读取上一交易日数据，并输出价格最低的 10 只合格可转债。

## 策略口径

候选券必须同时满足：

1. 正股不存在已知 ST、*ST 或退市风险；
2. 发行债项评级不低于 A+；
3. 距离合同到期日不少于 90 个自然日；
4. 最近 5 个交易所交易日的 `amount_for_screening` 合计不少于 5,000 万元；
5. 收盘价不低于 90 元；
6. 信号日具有正常交易数据；
7. 没有已经生效的“决定实施强赎”；
8. 尚未到达事件最后交易日或退市日。

合格券按照收盘价升序、转债代码升序排列，输出前 10 只。详细定义见 [`references/strategy_rules.md`](references/strategy_rules.md)。

## 仓库结构

```text
cb-low-price-daily/
├─ SKILL.md
├─ agents/
│  └─ openai.yaml
├─ references/
│  └─ strategy_rules.md
├─ scripts/
│  ├─ incremental_update.py
│  ├─ run_morning.py
│  └─ screen_daily.py
├─ requirements.txt
└─ .gitignore
```

## 运行环境

- Python 3.10 或更高版本；
- Windows、macOS 或 Linux；
- 能够访问 AKShare 所使用的公开数据接口；
- 一个已经建立好的可转债研究项目目录。

安装 Python 依赖：

```bash
python -m pip install -r requirements.txt
```

## 项目数据要求

本仓库不包含大体量历史行情和回测数据库。项目根目录需要存在以下文件：

```text
outputs/cb_master_20200101_20260911.parquet
outputs/daily/cb_daily_20200101_20260911.parquet
outputs/daily/cb_daily_warmup.parquet
outputs/daily/trade_calendar.parquet
outputs/events/cb_exit_events.parquet
outputs/backtest_framework/config.py
outputs/backtest_framework/data.py
outputs/backtest_framework/pool.py
```

文件名沿用原回测工程。若更改文件名或字段，需要同步修改脚本的数据读取逻辑。

## 手动运行

每天早晨执行以下命令：

```bash
python scripts/run_morning.py --project-root "D:/path/to/project"
```

程序将：

1. 确定运行日期之前最近一个交易所交易日；
2. 通过 AKShare 增量下载行情；
3. 更新正股风险和强赎快照；
4. 运行筛选；
5. 将结果写入 `outputs/daily_low_price/YYYY-MM-DD/`。

主要输出包括：

- `low_price_top10.csv`：前 10 名；
- `screening_detail.csv`：全量筛选明细；
- `screening_summary.csv`：各原因剔除数量；
- `run_metadata.json`：参数、日期和质量信息；
- `report.md`：每日文字报告；
- `outputs/daily_live/daily_update_failures.csv`：采集失败清单。

## 安装为 Codex Skill

克隆仓库后，将整个仓库目录复制到 Codex Skill 目录：

### Windows PowerShell

```powershell
Copy-Item -Recurse -Force `
  .\cb-low-price-daily `
  "$env:USERPROFILE\.codex\skills\cb-low-price-daily"
```

安装后，可以在 Codex 中使用：

```text
请使用 $cb-low-price-daily，更新上一交易日数据并输出低价策略前十名。
```

## 自动执行建议

可在 Codex 中建立每天早晨运行的自动任务。推荐时间为北京时间 08:00，任务内容如下：

```text
每天早上使用 $cb-low-price-daily。先运行 scripts/run_morning.py，读取上一交易日数据，再严格按照 references/strategy_rules.md 输出前十名、候选池数量、各项剔除数量和采集失败清单。数据未更新成功时必须报告实际数据日期，不得把旧数据称为上一交易日结果。
```

## 数据和回测限制

- 原回测在真实成交额缺失时使用“收盘价×成交量”估算成交额，结果会保留来源标记；
- 历史正股风险状态为 `unknown` 时，沿用原回测的放行口径；
- 发行评级不等于完整的历史动态评级；
- 当前证券全集依赖项目主表，新上市转债需要先纳入主表；
- 每日名单是策略信号，不构成投资建议，也不代表能够按收盘价实际成交。

## 上传 GitHub

在本目录打开终端，依次执行：

```bash
git init
git add .
git commit -m "Add cb-low-price-daily Codex skill"
git branch -M main
git remote add origin https://github.com/YOUR_NAME/cb-low-price-daily.git
git push -u origin main
```

执行前，需要先在 GitHub 创建一个空仓库，并把 `YOUR_NAME` 替换为自己的 GitHub 用户名。不要勾选自动创建 README，以免首次推送出现历史冲突。

如果仓库需要公开发布，请先自行选择并添加合适的开源许可证。本包没有替你决定授权方式。

