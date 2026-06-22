from controlflow_sdk.plane.__main__ import launch_banner


def test_launch_banner_names_both_entry_points():
    b = launch_banner("127.0.0.1", 8765)
    assert "http://127.0.0.1:8765" in b
    assert "controlplane" in b
    assert "python -m controlflow_sdk.plane" in b


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
