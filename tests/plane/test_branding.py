"""User-facing chrome reads 'Uticen' / 'Uticen Lite' with the logo. The full
rebrand to uticen-lite renamed the package, CLI, and bundle attribution too;
this file guards that the rendered chrome stays on-brand.

Note: the upgrade/packaging tests assert 'uticen-lite' / 'controlplane'."""


def test_header_shows_uticen_logo_and_lite(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "/static/uticen-logo.png" in page.text
    assert 'alt="Uticen"' in page.text
    assert ">Lite<" in page.text
    # the old text brand-mark is gone
    assert 'class="brand-mark">Uticen' not in page.text


def test_favicon_is_the_uticen_icon(client):
    page = client.get("/")
    assert "/static/uticen-icon.png" in page.text


def test_footer_rebranded(client):
    page = client.get("/")
    assert "Uticen Lite · local authoring surface" in page.text
    assert "Uticen Control Plane · local authoring surface" not in page.text


def test_logo_assets_ship(client):
    assert client.get("/static/uticen-logo.png").status_code == 200
    assert client.get("/static/uticen-icon.png").status_code == 200


def test_export_page_names_the_full_uticen_app(client):
    page = client.get("/export")
    assert page.status_code == 200
    assert "the Uticen app imports 1:1" in page.text
