"""HTTP analysis reads local data; only explicit sync workers may fetch upstream."""
from contextlib import contextmanager
from contextvars import ContextVar

_network_allowed = ContextVar('data_network_allowed', default=True)


def network_allowed() -> bool:
    return _network_allowed.get()


@contextmanager
def local_data_only():
    token = _network_allowed.set(False)
    try:
        yield
    finally:
        _network_allowed.reset(token)


def require_network() -> None:
    if not network_allowed():
        raise RuntimeError('本地数据尚未同步，请到左侧「数据同步」勾选所需行业和数据后同步。浏览页面不会自动联网拉取数据。')
