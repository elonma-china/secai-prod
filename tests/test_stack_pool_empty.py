from secai_gateway.poc import ias_pool


def test_empty_pool_does_not_crash(monkeypatch):
    # Không pool, không ias_server_url: gateway khởi động không profile exploit không được nổ.
    monkeypatch.setattr(ias_pool, "_initialized", False)
    monkeypatch.setattr(ias_pool, "_dynamic_pool", lambda: [])
    assert ias_pool._parse_pool("", "") == []
    assert ias_pool.pool_size("", "") == 0
