# A股银行股估值后端

FastAPI 银行股估值分析服务，输出估值区间、情景概率、蒙特卡洛价格分布与风险提示；不构成投资建议。

## 启动

在后端目录一键启动前后端（前端项目放在同级目录 `High-Value-Stock-Analysis-FrontEnd`）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

脚本会强制结束占用 `8000` 和 `5173` 端口的进程及其子进程，然后在后台启动两个项目并验证接口代理。前端地址为 `http://127.0.0.1:5173`，后端 API 文档为 `http://127.0.0.1:8000/docs`。重复运行即可重启，启动日志保存在 `logs/run/` 下。

脚本使用现有的 `.venv` 和前端 `node_modules`。首次安装或缺少依赖时，先在后端执行 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`，在前端执行 `.\install.cmd`。

单独启动后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn bank_valuation.app.main:app --reload
```

打开 `http://127.0.0.1:8000/docs` 查看并调试 API。

## 数据同步与本地浏览

左侧「数据同步」集中展示 8 个行业的本地数据覆盖，以及行情日期、财报报告期、银行监管指标和历史分红日期。银行范围包含排序、同行基准及回测使用的全部 42 家银行，其他行业使用核心股票池。

页面切换和分析 API 默认只读本地缓存，缓存过期不会触发联网刷新；实时行情轮询已暂停。缺少数据时，先在数据同步页勾选行业及数据类型，点击「同步所选」或「同步所有数据」。同步任务在独立进程中串行执行，页面保持可用；可立即暂停，并继续未完成任务或重试失败项。已有缓存不会因同步失败而被清空。

行情/复权日线同步完整可用历史；财务和银行监管指标同步指定日期前已公告的最新报告。历史分红展示最近除息日期，财报展示报告期，均不同于缓存更新时间。「已缓存」表示本地存在数据，最新日期以各行业及公司明细为准。行业研究先验、模型参数和情景假设随代码版本维护。

同步接口：`GET /api/data-sync/status`（只检查本地文件）、`POST /api/data-sync/start`（`industry_ids`、`dataset_ids`、可选 `target_date`；`resume=true` 继续上次未完成任务）、`POST /api/data-sync/pause`。进度及日志保存于 `output/data_sync/`，重启服务不会自动继续联网拉取。

## 简洁 API

单只银行只需代码和可选日期，后端会从 Baostock 自动取数：

```json
POST /api/bank/valuation
{"stock_code":"601398", "valuation_date":"2025-06-10"}
```

`valuation_date` 可省略，服务自动使用最近交易日。批量接口与蒙特卡洛接口使用相同的简单参数格式。

`POST /api/bank/industry-benchmark` 使用同样的参数，返回该银行相对 A 股银行样本的 PB、PE、年化 ROE、净利润同比的行业均值、中位数、四分位区间和横向百分位。首次按日期汇总会较慢，结果写入 `output/industry_benchmark/a_share_banks_YYYY-MM-DD.csv`，24 小时内优先读取。

日价格来自 Baostock `adjustflag="3"`（不复权的真实收盘价），`pbMRQ` 用作历史 PB；财务指标使用估值日已披露的最近报告，季报 ROE 自动年化。Baostock 未稳定提供的银行专属指标使用透明的内置默认假设。

银行专属数据不再使用统一默认值。服务会通过 AKShare 的东方财富财务指标接口严格选择估值日之前已公告的最新报告期，读取并转换 `NET_INTEREST_MARGIN`（净息差）、`NONPERLOAN`（不良率）、`BLDKBBL`（拨备覆盖率）、`HXYJBCZL`（核心一级资本充足率）和 `NEWCAPITALADER`（资本充足率）。接口百分数会转换为内部小数，结果记录报告期与来源、写入银行 CSV 快照；接口暂不可达或字段为空时明确返回“未获取”，不会伪造默认数值。无风险利率、ERP、Beta 和长期增长率则明确标记为估值模型假设，并不作为公司事实数据展示。

## 银行基础数据 CSV 缓存

首次查询会在 `output/bank_cache/` 写入两类按银行分组的 CSV：`*_snapshots.csv` 保存每个请求日期的一行基础财务与估值输入，`*_market_history.csv` 保存不复权收盘价和 PB 日线。再次查询相同代码和日期时，服务直接从 CSV 重建结果；查询新日期时会追加快照并合并新的日线记录。若需复核最新数据，可在请求中传入 `"refresh_cache": true` 强制重新查询 Baostock。

## 测试

```powershell
pytest -q
```

## 运行日志

启动服务后，数据获取和估值过程会输出到控制台，并写入 `logs/bank_valuation.log`。日志包含 Baostock 登录、查询返回行数、选中的交易日/财报期、模型结果以及异常堆栈。可通过 `LOG_LEVEL=DEBUG` 获得更详细输出，或通过 `BANK_VALUATION_LOG_DIR` 指定日志目录。
