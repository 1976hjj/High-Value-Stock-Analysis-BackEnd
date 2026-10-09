from datetime import date, datetime, timedelta
import json

import pytest
from fastapi.testclient import TestClient

from bank_valuation.app import data_sync
from bank_valuation.app.data_sources import bank_base, financial_snapshot, security_history, industry_benchmark
from bank_valuation.app.data_sources.access_policy import local_data_only
from bank_valuation.app.data_sources.industry_catalog import STOCK_CATALOG
from bank_valuation.app.main import app
from bank_valuation.app.valuation.strategy_backtest import _read_dividend_events


@pytest.fixture
def local_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(bank_base, '_BANK_CACHE_DIR', tmp_path / 'banks')
    monkeypatch.setattr(security_history, '_CACHE_DIR', tmp_path / 'market')
    monkeypatch.setattr(financial_snapshot, '_CACHE_DIR', tmp_path / 'financial')
    monkeypatch.setattr(industry_benchmark, '_BENCHMARK_CACHE_DIR', tmp_path / 'benchmark')
    monkeypatch.setattr(data_sync, 'SYNC_DIR', tmp_path / 'sync')
    monkeypatch.setattr(data_sync, 'JOB_PATH', tmp_path / 'sync' / 'job.json')
    monkeypatch.setattr(data_sync, '_process', None)
    bank_base._data_cache.clear()
    industry_benchmark._benchmark_cache.clear()
    return tmp_path


def cached_bank(bank):
    result = bank.model_copy(update={
        'stock_code': 'sh.601398', 'market_date': date(2025, 7, 10),
        'financial_report_date': date(2025, 3, 31),
        'pb_history_dates': [date(2025, 7, 9), date(2025, 7, 10)],
        'pb_history': [.65, .67], 'pe_history': [8.4, 8.5], 'price_history': [5.9, 6.0],
        'bank_special_metrics_source': 'AKShare / 东方财富',
        'bank_special_metrics_report_date': date(2025, 3, 31),
    })
    bank_base._write_disk_cache(date(2025, 7, 12), result)
    return result


def forbid_upstream(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail('Browsing must never fetch upstream data')
    monkeypatch.setattr(bank_base.bs, 'login', fail)
    monkeypatch.setattr(bank_base, 'fetch_bank_special_metrics', fail)


def test_inventory_is_local_and_reports_actual_dates(local_caches, bank, monkeypatch):
    cached_bank(bank)
    forbid_upstream(monkeypatch)
    response = TestClient(app).get('/api/data-sync/status')
    assert response.status_code == 200
    payload = response.json()
    assert payload['mode'] == 'local_only'
    assert payload['realtime_quotes'] == 'paused'
    assert len(payload['industries']) == 8
    industry = next(row for row in payload['industries'] if row['industry_id'] == 'bank')
    assert industry['stock_count'] == 42
    assert industry['latest_date'] == '2025-07-10'
    stock = next(row for row in industry['stocks'] if row['stock_code'] == '601398')
    assert stock['datasets']['financial']['latest_date'] == '2025-03-31'
    assert stock['datasets']['market_raw']['row_count'] == 2
    assert stock['datasets']['bank_metrics']['status'] == 'partial'
    assert not data_sync.JOB_PATH.exists()


def test_bank_browsing_uses_old_snapshot_and_never_enriches(local_caches, bank, monkeypatch):
    cached_bank(bank)
    forbid_upstream(monkeypatch)
    response = TestClient(app).post('/api/bank/valuation', json={
        'stock_code': '601398', 'valuation_date': '2026-10-04', 'include_full_history': True,
    })
    assert response.status_code == 200
    assert response.json()['market_date'] == '2025-07-10'
    assert response.json()['financial_report_date'] == '2025-03-31'
    refresh = TestClient(app).post('/api/bank/valuation', json={'stock_code': '601398', 'refresh_cache': True})
    assert refresh.status_code == 422
    assert '数据同步' in refresh.json()['detail']


def test_cache_missing_requests_and_dividends_never_fetch(local_caches, monkeypatch):
    forbid_upstream(monkeypatch)
    response = TestClient(app).post('/api/industry/analysis', json={'industry_id': 'hydro', 'stock_code': '600900'})
    assert response.status_code == 422
    assert '数据同步' in response.json()['detail']
    with local_data_only(), pytest.raises(RuntimeError, match='数据同步'):
        _read_dividend_events('sh.600900')


def test_financial_cache_ttl_does_not_trigger_network_or_future_data(local_caches, monkeypatch):
    stock = STOCK_CATALOG['sh.600900']
    snap = financial_snapshot.FinancialSnapshot(stock.code, date(2025, 3, 31), date(2025, 4, 29), 2025, 1, {'roeAvg': .05}, .5)
    financial_snapshot._write_cache(snap, date(2025, 7, 12))
    path = financial_snapshot._cache_path(stock.code, date(2025, 7, 12))
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['fetched_at'] = (datetime.now() - timedelta(days=90)).isoformat()
    path.write_text(json.dumps(payload), encoding='utf-8')
    forbid_upstream(monkeypatch)
    with local_data_only():
        restored = financial_snapshot.load_financial_snapshot(stock, date(2026, 10, 4))
        assert restored.report_date == date(2025, 3, 31)
        with pytest.raises(RuntimeError, match='数据同步'):
            financial_snapshot.load_financial_snapshot(stock, date(2025, 1, 1))


def test_local_market_cache_does_not_need_full_requested_coverage(local_caches, monkeypatch):
    stock = STOCK_CATALOG['sh.600900']
    points = {date(2025, 7, 10): security_history.SecurityMarketPoint(25.0, 2.5, 18.0)}
    security_history._write_cache(stock.code, 'raw', points, date(2025, 7, 1), date(2025, 7, 12))
    forbid_upstream(monkeypatch)
    with local_data_only():
        loaded = security_history.load_market_histories([stock], date(2020, 1, 1), date(2026, 10, 4), adjustment='raw')
    assert loaded.histories[stock.code] == points
    assert loaded.failures == []


def test_task_plan_includes_whole_bank_universe_without_duplicate_fetches():
    tasks = data_sync.make_tasks(['bank', 'bank'], ['market_raw', 'financial', 'bank_metrics', 'market_post', 'dividends', 'benchmark'])
    assert len([row for row in tasks if row['dataset_id'] == 'bank_snapshot']) == 42
    assert len([row for row in tasks if row['dataset_id'] == 'market_post']) == 6
    assert len({row['id'] for row in tasks}) == len(tasks)
    with pytest.raises(ValueError):
        data_sync.make_tasks(['not-an-industry'], ['market_raw'])


class FakeProcess:
    def __init__(self, *args, **kwargs):
        self.stopped = False
    def poll(self):
        return 0 if self.stopped else None
    def terminate(self):
        self.stopped = True
    def wait(self, timeout=None):
        return 0


def test_single_job_pause_and_resume_preserve_completed_work(local_caches, monkeypatch):
    monkeypatch.setattr(data_sync.subprocess, 'Popen', FakeProcess)
    job = data_sync.start_sync(['telecom'], ['market_raw'], date(2025, 7, 12))
    with pytest.raises(RuntimeError, match='已有同步任务'):
        data_sync.start_sync(['hydro'], ['financial'])
    job['tasks'][0]['status'] = 'completed'
    job['tasks'][1]['status'] = 'running'
    job['completed'] = 1
    data_sync.save_job(job)
    paused = data_sync.pause_sync()
    assert paused['state'] == 'paused'
    assert paused['tasks'][0]['status'] == 'completed'
    assert paused['tasks'][1]['status'] == 'paused'
    resumed = data_sync.start_sync(['telecom'], ['market_raw'], resume=True)
    assert resumed['completed'] == 1
    assert resumed['tasks'][0]['status'] == 'completed'
    assert resumed['target_date'] == '2025-07-12'
    called = []
    monkeypatch.setattr(data_sync, 'execute_task', lambda task, target: called.append(task['id']))
    data_sync.run_worker(resumed['id'])
    completed = data_sync.read_job()
    assert completed['state'] == 'completed'
    assert completed['completed'] == completed['total'] == 3
    assert len(called) == 2


def test_failed_task_does_not_abort_remaining_data(local_caches, monkeypatch):
    monkeypatch.setattr(data_sync.subprocess, 'Popen', FakeProcess)
    job = data_sync.start_sync(['telecom'], ['financial'])
    def execute(task, target):
        if task['stock_code'] == 'sh.600941':
            raise RuntimeError('offline fixture failure')
    monkeypatch.setattr(data_sync, 'execute_task', execute)
    data_sync.run_worker(job['id'])
    result = data_sync.read_job()
    assert result['state'] == 'partial'
    assert result['completed'] == 3
    assert result['failed'] == 1
    assert result['tasks'][1]['status'] == 'completed'


def test_sync_endpoint_validation_and_conflict(local_caches, monkeypatch):
    monkeypatch.setattr(data_sync.subprocess, 'Popen', FakeProcess)
    client = TestClient(app)
    assert client.post('/api/data-sync/start', json={'industry_ids': [], 'dataset_ids': ['financial']}).status_code == 422
    payload = {'industry_ids': ['hydro'], 'dataset_ids': ['financial']}
    assert client.post('/api/data-sync/start', json=payload).status_code == 202
    assert client.post('/api/data-sync/start', json=payload).status_code == 409
    assert client.post('/api/data-sync/pause').json()['job']['state'] == 'paused'


def test_windows_pause_stops_child_python_processes(monkeypatch):
    process = FakeProcess()
    process.pid = 43210
    commands = []
    def stop_tree(command, **kwargs):
        commands.append(command)
        process.stopped = True
        return type('Result', (), {'returncode': 0})()
    monkeypatch.setattr(data_sync.os, 'name', 'nt')
    monkeypatch.setattr(data_sync.subprocess, 'run', stop_tree)
    data_sync.terminate_worker(process)
    assert commands == [['taskkill', '/PID', '43210', '/T', '/F']]
    assert process.stopped
