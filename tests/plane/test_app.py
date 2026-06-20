def test_dashboard_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Acme" in resp.text
    assert "New control" in resp.text


def test_static_css_served(client):
    assert client.get("/static/app.css").status_code == 200


def test_favicon_served(client):
    # The browser auto-requests /favicon.ico on every page; serve it so it
    # doesn't 404 in the console.
    resp = client.get("/favicon.ico")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
