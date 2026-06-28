"""User-facing chrome reads 'Uticen' / 'Uticen Lite' with the logo; the package
name, CLI, and bundle attribution are NOT renamed (2026-06-27 review).

Note: the existing upgrade/packaging tests assert 'controlflow-sdk' / 'controlplane'
and would fail if those were renamed — so this file guards the rebrand surface."""


def test_header_shows_uticen_logo_and_lite(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "/static/uticen-logo.png" in page.text
    assert 'alt="Uticen"' in page.text
    assert ">Lite<" in page.text
    # the old text brand-mark is gone
    assert 'class="brand-mark">ControlFlow' not in page.text


def test_favicon_is_the_uticen_icon(client):
    page = client.get("/")
    assert "/static/uticen-icon.png" in page.text


def test_footer_rebranded(client):
    page = client.get("/")
    assert "Uticen Lite · local authoring surface" in page.text
    assert "ControlFlow Control Plane · local authoring surface" not in page.text


def test_logo_assets_ship(client):
    assert client.get("/static/uticen-logo.png").status_code == 200
    assert client.get("/static/uticen-icon.png").status_code == 200


def test_export_page_names_the_full_uticen_app(client):
    page = client.get("/export")
    assert page.status_code == 200
    assert "the Uticen app imports 1:1" in page.text
    assert "ControlFlow app imports" not in page.text
