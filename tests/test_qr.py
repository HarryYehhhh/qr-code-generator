class TestCreateQRCode:
    def test_create_success(self, client):
        resp = client.post("/v1/qr_code", json={"url": "https://example.com"})
        assert resp.status_code == 201
        assert "qr_token" in resp.json()
        assert len(resp.json()["qr_token"]) == 10

    def test_create_invalid_url(self, client):
        resp = client.post("/v1/qr_code", json={"url": "not-a-url"})
        assert resp.status_code == 422

    def test_create_non_ascii_url(self, client):
        resp = client.post("/v1/qr_code", json={"url": "https://例.com"})
        assert resp.status_code == 422

    def test_create_pre_warms_redis_cache(self, client, fake_redis):
        resp = client.post("/v1/qr_code", json={"url": "https://example.com"})
        token = resp.json()["qr_token"]
        assert fake_redis.get(f"qr:url:{token}") == b"https://example.com"


class TestGetQRCode:
    def test_get_success(self, client):
        token = client.post("/v1/qr_code", json={"url": "https://example.com"}).json()["qr_token"]
        resp = client.get(f"/v1/qr_code/{token}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == "https://example.com"
        assert data["click_count"] == 0
        assert data["status"] == "active"
        assert "created_at" in data

    def test_get_not_found(self, client):
        resp = client.get("/v1/qr_code/nonexistent")
        assert resp.status_code == 404

    def test_get_deleted_returns_410(self, client):
        token = client.post("/v1/qr_code", json={"url": "https://example.com"}).json()["qr_token"]
        client.delete(f"/v1/qr_code/{token}")
        resp = client.get(f"/v1/qr_code/{token}")
        assert resp.status_code == 410


class TestUpdateQRCode:
    def test_update_success(self, client):
        token = client.post("/v1/qr_code", json={"url": "https://old.com"}).json()["qr_token"]
        resp = client.put(f"/v1/qr_code/{token}", json={"url": "https://new.com"})
        assert resp.status_code == 204

        resp = client.get(f"/v1/qr_code/{token}")
        assert resp.json()["url"] == "https://new.com"

    def test_update_not_found(self, client):
        resp = client.put("/v1/qr_code/nonexistent", json={"url": "https://new.com"})
        assert resp.status_code == 404


class TestDeleteQRCode:
    def test_delete_success(self, client):
        token = client.post("/v1/qr_code", json={"url": "https://example.com"}).json()["qr_token"]
        resp = client.delete(f"/v1/qr_code/{token}")
        assert resp.status_code == 204

        # 刪除後回傳 410（soft delete）
        resp = client.get(f"/v1/qr_code/{token}")
        assert resp.status_code == 410

    def test_delete_not_found(self, client):
        resp = client.delete("/v1/qr_code/nonexistent")
        assert resp.status_code == 404


class TestQRCodeImage:
    def test_generate_image(self, client):
        token = client.post("/v1/qr_code", json={"url": "https://example.com"}).json()["qr_token"]
        resp = client.get(f"/v1/qr_code_image/{token}?dimension=128&color=%23ff0000&border=2")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        # PNG magic bytes
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_image_not_found(self, client):
        resp = client.get("/v1/qr_code_image/nonexistent")
        assert resp.status_code == 404

    def test_image_cache_hit(self, client, fake_redis):
        token = client.post("/v1/qr_code", json={"url": "https://example.com"}).json()["qr_token"]

        resp1 = client.get(f"/v1/qr_code_image/{token}?dimension=128")
        resp2 = client.get(f"/v1/qr_code_image/{token}?dimension=128")

        assert resp1.content == resp2.content
        # at least one image cache key exists
        keys = list(fake_redis.scan_iter(match="qr:img:*"))
        assert len(keys) >= 1

    def test_image_regenerates_after_url_update(self, client):
        token = client.post("/v1/qr_code", json={"url": "https://old.com"}).json()["qr_token"]
        resp1 = client.get(f"/v1/qr_code_image/{token}?dimension=128")

        client.put(f"/v1/qr_code/{token}", json={"url": "https://new.com"})
        resp2 = client.get(f"/v1/qr_code_image/{token}?dimension=128")

        # different URL → different encoded QR → different bytes
        assert resp1.content != resp2.content


class TestRedirect:
    def test_redirect_success(self, client):
        token = client.post("/v1/qr_code", json={"url": "https://example.com"}).json()["qr_token"]
        resp = client.get(f"/r/{token}", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "https://example.com"

    def test_redirect_not_found(self, client):
        resp = client.get("/r/nonexistent", follow_redirects=False)
        assert resp.status_code == 404

    def test_redirect_records_click_in_redis(self, client, fake_redis):
        from datetime import datetime, timezone

        token = client.post("/v1/qr_code", json={"url": "https://example.com"}).json()["qr_token"]
        client.get(f"/r/{token}", follow_redirects=False)
        client.get(f"/r/{token}", follow_redirects=False)

        hour = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
        count = fake_redis.hget(f"qr:clicks:{hour}", token)
        assert int(count) == 2

    def test_redirect_cache_hit(self, client, fake_redis):
        token = client.post("/v1/qr_code", json={"url": "https://example.com"}).json()["qr_token"]
        # first hit populates cache
        client.get(f"/r/{token}", follow_redirects=False)
        # simulate cache hit by checking key exists
        assert fake_redis.get(f"qr:url:{token}") == b"https://example.com"
        # second redirect still works from cache
        resp = client.get(f"/r/{token}", follow_redirects=False)
        assert resp.status_code == 302

    def test_redirect_update_invalidates_cache(self, client, fake_redis):
        token = client.post("/v1/qr_code", json={"url": "https://old.com"}).json()["qr_token"]
        client.get(f"/r/{token}", follow_redirects=False)
        assert fake_redis.get(f"qr:url:{token}") is not None

        client.put(f"/v1/qr_code/{token}", json={"url": "https://new.com"})
        assert fake_redis.get(f"qr:url:{token}") is None

        resp = client.get(f"/r/{token}", follow_redirects=False)
        assert resp.headers["location"] == "https://new.com"

    def test_redirect_delete_invalidates_cache(self, client, fake_redis):
        token = client.post("/v1/qr_code", json={"url": "https://example.com"}).json()["qr_token"]
        client.get(f"/r/{token}", follow_redirects=False)
        assert fake_redis.get(f"qr:url:{token}") is not None

        client.delete(f"/v1/qr_code/{token}")
        assert fake_redis.get(f"qr:url:{token}") is None

    def test_redirect_deleted_returns_410(self, client):
        token = client.post("/v1/qr_code", json={"url": "https://example.com"}).json()["qr_token"]
        client.delete(f"/v1/qr_code/{token}")
        resp = client.get(f"/r/{token}", follow_redirects=False)
        assert resp.status_code == 410
