def test_dashboard_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Acme" in resp.text
    assert "New control" in resp.text


def test_static_css_served(client):
    assert client.get("/static/app.css").status_code == 200
