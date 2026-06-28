# A股银行股估值后端

FastAPI 银行股估值分析服务，输出估值区间、情景概率、蒙特卡洛价格分布与风险提示；不构成投资建议。

## 启动

```powershell
pip install -r requirements.txt
uvicorn bank_valuation.app.main:app --reload
```

打开 `http://127.0.0.1:8000/docs` 查看并调试 API。

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
