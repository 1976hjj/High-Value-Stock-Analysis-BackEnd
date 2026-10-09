"""Local cache inventory and an explicit, cancellable data synchronization worker."""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

from .data_sources import bank_base, financial_snapshot, industry_benchmark, security_history
from .data_sources.industry_catalog import INDUSTRY_CATALOG, INDUSTRY_ORDER, STOCK_CATALOG, IndustryStockProfile

ROOT = Path(__file__).resolve().parents[2]
SYNC_DIR = ROOT / 'output' / 'data_sync'
JOB_PATH = SYNC_DIR / 'job.json'
_lock = threading.RLock()
_process: subprocess.Popen | None = None

DATASETS = [
    {'id': 'market_raw', 'label': '行情与 PB / PE', 'source': 'Baostock', 'description': '不复权收盘价、历史 PB / PE，用于估值与数据全景。'},
    {'id': 'market_post', 'label': '复权日线', 'source': 'Baostock', 'description': '核心股票池后复权价格，用于风险指标和跨行业回测。'},
    {'id': 'financial', 'label': '财报与现金分红', 'source': 'Baostock / 东方财富', 'description': '已公告财报、盈利、现金流、资产负债和每股分红；日期为财报报告期。'},
    {'id': 'bank_metrics', 'label': '银行监管指标', 'source': 'AKShare / 东方财富', 'description': '净息差、不良率、拨备、资本充足率、存贷比；仅适用于银行。'},
    {'id': 'dividends', 'label': '历史分红事件', 'source': 'AKShare / 东方财富', 'description': '公告日期、除息日期及每股现金分红，用于回测股息归因。'},
    {'id': 'benchmark', 'label': '银行横向基准', 'source': '本地银行快照汇总', 'description': '全部 A 股银行 PB / PE / ROE 与监管指标比较；不会重复逐家联网查询。'},
]


def now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


def today() -> date:
    return datetime.now(timezone(timedelta(hours=8))).date()


def save_job(job: dict) -> None:
    JOB_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = JOB_PATH.with_suffix('.tmp')
    temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(JOB_PATH)


def read_job() -> dict | None:
    if not JOB_PATH.exists():
        return None
    try:
        return json.loads(JOB_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def job_status() -> dict | None:
    with _lock:
        job = read_job()
        if job and job['state'] == 'running' and (_process is None or _process.poll() is not None):
            job['state'] = 'interrupted'
            job['finished_at'] = now()
            job['message'] = '同步进程已停止，已完成的数据保留；可重新选择并同步。'
            save_job(job)
        return job


def stocks_for(industry_id: str) -> list[IndustryStockProfile]:
    if industry_id != 'bank':
        return list(INDUSTRY_CATALOG[industry_id].stocks)
    # Ranking, benchmarks and bank backtests use all 42 banks, beyond the 6 core banks.
    return [STOCK_CATALOG.get(code) or IndustryStockProfile(code, name, 'bank', 0, 0, 0, 0)
            for code, name in bank_base.BANK_NAMES.items()]


@lru_cache(maxsize=1024)
def _inspect_file(path_text: str, modified: int, size: int, date_field: str) -> dict:
    path = Path(path_text)
    if path.suffix == '.json':
        payload = json.loads(path.read_text(encoding='utf-8'))
        snapshot = payload['snapshot']
        return {'latest_date': snapshot['report_date'], 'first_date': snapshot['report_date'],
                'row_count': 1, 'published_date': snapshot['published_date'], 'updated_at': payload['fetched_at'],
                'latest_row': snapshot, 'columns': list(snapshot.get('values', {}))}
    count, first, latest, latest_row = 0, None, None, {}
    metric_counts = {}
    with path.open(encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        columns = reader.fieldnames or []
        for row in reader:
            value = row.get(date_field)
            if not value:
                continue
            parsed = date.fromisoformat(value).isoformat()
            if date_field == 'date' and float(row.get('close') or 0) <= 0:
                continue
            count += 1
            if row.get('metric'):
                metric_counts[row['metric']] = metric_counts.get(row['metric'], 0) + 1
            first = min(first, parsed) if first else parsed
            if latest is None or parsed >= latest:
                latest, latest_row = parsed, row
    return {'latest_date': latest, 'first_date': first, 'row_count': count, 'latest_row': latest_row,
            'columns': columns, 'metric_counts': metric_counts, 'updated_at': datetime.fromtimestamp(modified / 1e9, timezone(timedelta(hours=8))).isoformat(timespec='seconds')}


def inspect_file(path: Path, date_field: str = 'date') -> dict:
    if not path.exists():
        return {'status': 'missing', 'latest_date': None, 'first_date': None, 'row_count': 0, 'updated_at': None}
    try:
        stat = path.stat()
        result = dict(_inspect_file(str(path.resolve()), stat.st_mtime_ns, stat.st_size, date_field))
        result['status'] = 'cached' if result['row_count'] else 'missing'
        return result
    except (OSError, KeyError, TypeError, ValueError):
        return {'status': 'error', 'latest_date': None, 'first_date': None, 'row_count': 0, 'updated_at': None,
                'error': '缓存不可读，需要重新同步'}


def stock_dataset(stock: IndustryStockProfile, kind: str) -> dict:
    if kind == 'market_raw':
        path = bank_base._market_path(stock.code) if stock.industry_id == 'bank' else security_history._cache_path(stock.code, 'raw')
        result = inspect_file(path)
    elif kind == 'market_post':
        if stock.code not in STOCK_CATALOG:
            return {'status': 'not_applicable', 'latest_date': None, 'row_count': 0}
        result = inspect_file(security_history._cache_path(stock.code, 'post'))
    elif kind in {'financial', 'bank_metrics'} and stock.industry_id == 'bank':
        result = inspect_file(bank_base._snapshot_path(stock.code), 'requested_date')
        row = result.get('latest_row', {})
        field = 'financial_report_date' if kind == 'financial' else 'bank_special_metrics_report_date'
        result['latest_date'] = row.get(field) if row.get(field) not in {'None', ''} else None
        result['first_date'] = result['latest_date']
        if not result['latest_date']:
            result['status'] = 'missing'
        if kind == 'bank_metrics':
            keys = ['nim', 'npl_ratio', 'provision_coverage', 'cet1_ratio', 'capital_adequacy_ratio',
                    'net_interest_spread', 'loan_provision_ratio', 'loan_to_deposit_ratio']
            result['field_count'] = sum(row.get(key) not in {None, '', 'None'} for key in keys)
            result['field_total'] = len(keys)
            source = row.get('bank_special_metrics_source')
            if source in {None, '', 'None'} or not result['latest_date']:
                result['status'] = 'missing'
            elif result['field_count'] < len(keys):
                result['status'] = 'partial'
    elif kind == 'financial':
        paths = sorted(financial_snapshot._CACHE_DIR.glob(f"{stock.code.replace('.', '_')}_*.json"), reverse=True)
        result = inspect_file(paths[0]) if paths else {'status': 'missing', 'latest_date': None, 'row_count': 0}
    elif kind == 'dividends':
        path = bank_base._market_path(stock.code).with_name(f"{stock.code.replace('.', '_')}_dividend_events.csv")
        result = inspect_file(path, 'ex_dividend_date')
    else:
        return {'status': 'not_applicable', 'latest_date': None, 'row_count': 0}
    result.pop('latest_row', None)
    result.pop('columns', None)
    return result


def inventory() -> dict:
    industries = []
    for industry_id in INDUSTRY_ORDER:
        stocks = [{'stock_code': stock.code.split('.')[1], 'stock_name': stock.name,
                   'datasets': {kind['id']: stock_dataset(stock, kind['id']) for kind in DATASETS if kind['id'] != 'benchmark'}}
                  for stock in stocks_for(industry_id)]
        datasets = []
        for definition in DATASETS:
            kind = definition['id']
            if kind == 'benchmark':
                if industry_id != 'bank':
                    continue
                paths = sorted(industry_benchmark._BENCHMARK_CACHE_DIR.glob('a_share_banks_*.csv'), reverse=True)
                entries = [inspect_file(paths[0], 'as_of_date')] if paths else [{'status': 'missing', 'latest_date': None}]
            else:
                entries = [stock['datasets'][kind] for stock in stocks if stock['datasets'][kind]['status'] != 'not_applicable']
            if not entries:
                continue
            dates = [entry['latest_date'] for entry in entries if entry.get('latest_date')]
            available = sum(entry['status'] in {'cached', 'partial'} for entry in entries)
            partial = sum(entry['status'] == 'partial' for entry in entries)
            total = len(entries)
            if kind == 'benchmark':
                total = len(bank_base.BANK_NAMES)
                available = min(entries[0].get('metric_counts', {}).get('pb', 0), total)
                partial = 0
            datasets.append({**definition, 'available': available, 'total': total, 'partial': partial,
                             'status': 'cached' if available == total and not partial else 'partial' if available else 'missing',
                             'latest_date': max(dates) if dates else None, 'oldest_date': min(dates) if dates else None})
        market = next(row for row in datasets if row['id'] == 'market_raw')
        industries.append({'industry_id': industry_id, 'name': INDUSTRY_CATALOG[industry_id].name,
                           'stock_count': len(stocks), 'latest_date': market['latest_date'], 'oldest_date': market['oldest_date'],
                           'available': sum(row['available'] for row in datasets), 'total': sum(row['total'] for row in datasets),
                           'datasets': datasets, 'stocks': stocks})
    return {'mode': 'local_only', 'checked_at': now(), 'target_date': today().isoformat(),
            'datasets': DATASETS, 'industries': industries, 'job': job_status(),
            'static_data': ['行业及股票池', '公司研究先验', '估值模型参数与情景假设'],
            'realtime_quotes': 'paused'}


def make_tasks(industry_ids: list[str], dataset_ids: list[str]) -> list[dict]:
    unknown = set(industry_ids) - set(INDUSTRY_ORDER)
    unknown_data = set(dataset_ids) - {row['id'] for row in DATASETS}
    if unknown or unknown_data or not industry_ids or not dataset_ids:
        raise ValueError('请选择有效的行业和数据类型。')
    tasks = []
    for industry_id in dict.fromkeys(industry_ids):
        for stock in stocks_for(industry_id):
            kinds = []
            if industry_id == 'bank' and set(dataset_ids) & {'market_raw', 'financial', 'bank_metrics'}:
                kinds.append('bank_snapshot')
            elif industry_id != 'bank':
                kinds.extend(kind for kind in ('market_raw', 'financial') if kind in dataset_ids)
            kinds.extend(kind for kind in ('market_post', 'dividends') if kind in dataset_ids
                         and (kind != 'market_post' or stock.code in STOCK_CATALOG))
            for kind in kinds:
                label = '银行行情、财报与监管指标' if kind == 'bank_snapshot' else next(row['label'] for row in DATASETS if row['id'] == kind)
                tasks.append({'id': f'{stock.code}:{kind}', 'industry_id': industry_id, 'stock_code': stock.code,
                              'stock_name': stock.name, 'dataset_id': kind, 'label': label, 'status': 'pending', 'error': None})
    if 'bank' in industry_ids and 'benchmark' in dataset_ids:
        tasks.append({'id': 'bank:benchmark', 'industry_id': 'bank', 'stock_code': '', 'stock_name': '全部 A 股银行',
                      'dataset_id': 'benchmark', 'label': '银行横向基准', 'status': 'pending', 'error': None})
    if not tasks:
        raise ValueError('所选数据不适用于这些行业，请调整选择。')
    return tasks


def start_sync(industry_ids: list[str], dataset_ids: list[str], target_date: date | None = None, resume: bool = False) -> dict:
    global _process
    target_date = target_date or today()
    if target_date > today() or target_date < date(1990, 1, 1):
        raise ValueError('同步日期必须在 1990-01-01 与今天之间。')
    tasks = make_tasks(industry_ids, dataset_ids)
    with _lock:
        existing = job_status()
        if existing and existing['state'] == 'running':
            raise RuntimeError('已有同步任务正在运行，请先暂停或等待完成。')
        if resume:
            if not existing or existing['state'] not in {'paused', 'interrupted', 'partial'}:
                raise ValueError('没有可继续的同步任务。')
            tasks = existing['tasks']
            target_date = date.fromisoformat(existing['target_date'])
            industry_ids, dataset_ids = existing['industry_ids'], existing['dataset_ids']
            for task in tasks:
                if task['status'] != 'completed':
                    task.update(status='pending', error=None)
            if all(task['status'] == 'completed' for task in tasks):
                raise ValueError('所有任务已完成。')
        job = {'id': uuid.uuid4().hex, 'state': 'running', 'target_date': target_date.isoformat(),
               'industry_ids': industry_ids, 'dataset_ids': dataset_ids,
               'started_at': now(), 'finished_at': None, 'total': len(tasks), 'completed': sum(task['status'] == 'completed' for task in tasks), 'failed': 0,
               'current': None, 'tasks': tasks, 'message': '准备同步，浏览页面继续使用已有缓存。'}
        save_job(job)
        log_path = SYNC_DIR / f"{job['id']}.log"
        try:
            with log_path.open('w', encoding='utf-8') as log:
                _process = subprocess.Popen([sys.executable, '-m', 'bank_valuation.app.data_sync', job['id']],
                                            cwd=ROOT, stdout=log, stderr=log,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except Exception:
            job.update(state='failed', finished_at=now(), message='无法启动同步进程，请检查运行环境。')
            save_job(job)
            raise
        return job


def terminate_worker(process) -> None:
    # Windows venv launchers create another Python process: terminate the tree,
    # otherwise killing only the launcher leaves an upstream fetch running.
    if os.name == 'nt' and getattr(process, 'pid', None):
        result = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode and process.poll() is None:
            raise RuntimeError('无法停止同步进程，请重试暂停。')
    else:
        process.terminate()
    process.wait(timeout=10)


def pause_sync() -> dict | None:
    global _process
    with _lock:
        job = read_job()
        if job and job['state'] == 'running':
            if _process is not None and _process.poll() is None:
                terminate_worker(_process)
            job = read_job() or job
            if job['state'] == 'running':
                job.update(state='paused', finished_at=now(), current=None, message='已暂停，已完成的数据保留，不再继续拉取。')
                for task in job['tasks']:
                    if task['status'] == 'running':
                        task['status'] = 'paused'
                save_job(job)
        _process = None
        return job


def execute_task(task: dict, target: date) -> None:
    kind, code = task['dataset_id'], task['stock_code']
    if kind == 'benchmark':
        from .valuation.mean_reversion import _load_latest_cached_bank
        mapping = {'pb': 'pb_current', 'pe': 'pe_current', 'roe': 'roe', 'profit_growth_yoy': 'profit_growth_yoy',
                   'nim': 'nim', 'net_interest_spread': 'net_interest_spread', 'npl_ratio': 'npl_ratio',
                   'provision_coverage': 'provision_coverage', 'loan_provision_ratio': 'loan_provision_ratio',
                   'cet1_ratio': 'cet1_ratio', 'capital_adequacy_ratio': 'capital_adequacy_ratio', 'loan_to_deposit_ratio': 'loan_to_deposit_ratio'}
        peers = {}
        banks = []
        for bank_code in bank_base.BANK_NAMES:
            try:
                banks.append(_load_latest_cached_bank(bank_code, target))
            except RuntimeError:
                pass
        if not banks:
            raise RuntimeError('请先同步银行行情、财报与监管指标，再生成横向基准。')
        as_of = min(bank.market_date for bank in banks if bank.market_date)
        for key, field in mapping.items():
            values = [getattr(bank, field) for bank in banks if bank.market_date == as_of and getattr(bank, field) is not None]
            if values:
                peers[key] = values
        industry_benchmark._write_csv_cache(as_of, peers)
        return
    stock = next(stock for stock in stocks_for(task['industry_id']) if stock.code == code)
    if kind == 'bank_snapshot':
        bank = bank_base.load_bank_input(code, target, refresh_cache=True, history_years=None, include_full_history=True)
        history = {day: security_history.SecurityMarketPoint(bank.price_history[index], bank.pb_history[index], bank.pe_history[index] if index < len(bank.pe_history) else None)
                   for index, day in enumerate(bank.pb_history_dates)}
        if history:
            security_history._write_cache(code, 'raw', history, date(1990, 1, 1), target)
    elif kind in {'market_raw', 'market_post'}:
        adjustment = 'raw' if kind == 'market_raw' else 'post'
        result = security_history.load_market_histories([stock], date(1990, 1, 1), target,
                                                       adjustment=adjustment, refresh_cache=True)
        if result.failures or not result.histories.get(code):
            raise RuntimeError(result.failures[0]['error'] if result.failures else '数据源未返回日线')
    elif kind == 'financial':
        financial_snapshot.load_financial_snapshot(stock, target, refresh_cache=True)
    elif kind == 'dividends':
        from .valuation.strategy_backtest import _fetch_dividend_events, _write_dividend_events_cache
        events = _fetch_dividend_events(code)
        if not events:
            raise RuntimeError('数据源未返回可核验的分红事件，保留原缓存。')
        _write_dividend_events_cache(code, events)


def run_worker(job_id: str) -> None:
    job = read_job()
    if not job or job['id'] != job_id or job['state'] != 'running':
        return
    target = date.fromisoformat(job['target_date'])
    for task in job['tasks']:
        if task['status'] == 'completed':
            continue
        task['status'] = 'running'
        job['current'] = task['id']
        job['message'] = f"正在同步 {task['stock_name']} · {task['label']}"
        save_job(job)
        try:
            execute_task(task, target)
            task['status'] = 'completed'
        except Exception as exc:
            task['status'], task['error'] = 'failed', str(exc)
            job['failed'] += 1
        job['completed'] += 1
        save_job(job)
    job.update(state='partial' if job['failed'] else 'completed', finished_at=now(), current=None,
               message=f"同步结束：成功 {job['completed'] - job['failed']}，失败 {job['failed']}。")
    save_job(job)


if __name__ == '__main__':
    run_worker(sys.argv[1])
