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


class TestGetQRCode:
    def test_get_success(self, client):
        token = client.post("/v1/qr_code", json={"url": "https://example.com"}).json()["qr_token"]
        resp = client.get(f"/v1/qr_code/{token}")
        assert resp.status_code == 200
        assert resp.json()["url"] == "https://example.com"

    def test_get_not_found(self, client):
        resp = client.get("/v1/qr_code/nonexistent")
        assert resp.status_code == 404


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

        # 刪除後查不到（soft delete）
        resp = client.get(f"/v1/qr_code/{token}")
        assert resp.status_code == 404

    def test_delete_not_found(self, client):
        resp = client.delete("/v1/qr_code/nonexistent")
        assert resp.status_code == 404


class TestQRCodeImage:
    def test_generate_image(self, client):
        token = client.post("/v1/qr_code", json={"url": "https://example.com"}).json()["qr_token"]
        resp = client.get(f"/v1/qr_code_image/{token}?dimension=128&color=%23ff0000&border=2")
        assert resp.status_code == 200
        assert "image_location" in resp.json()
        assert token in resp.json()["image_location"]

    def test_image_not_found(self, client):
        resp = client.get("/v1/qr_code_image/nonexistent")
        assert resp.status_code == 404


class TestRedirect:
    def test_redirect_success(self, client):
        token = client.post("/v1/qr_code", json={"url": "https://example.com"}).json()["qr_token"]
        resp = client.get(f"/{token}", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "https://example.com"

    def test_redirect_not_found(self, client):
        resp = client.get("/nonexistent", follow_redirects=False)
        assert resp.status_code == 404
