"""Definition-tab source binding: a searchable "+" picker replaces the
checkbox list, and add/remove swap a fragment in place (no full reload → no
scroll-to-top). 2026-06-27 review."""
import io


def _make_source(client, sid, title=None):
    data = {"source_id": sid, "format": "csv"}
    client.post("/sources", data=data,
                files={"file": (f"{sid}.csv", io.BytesIO(b"k\n1\n"), "text/csv")},
                follow_redirects=False)


def _control(client, sid="users"):
    client.post("/controls", data={"id": "c1", "title": "C1", "objective": "o",
                "narrative": "n", "source_ids": [sid], "failure_threshold_count": "0"},
                follow_redirects=False)


def test_existing_control_renders_chips_and_picker(client):
    _make_source(client, "users")
    _make_source(client, "other")
    _control(client, "users")
    page = client.get("/controls/c1")
    assert page.status_code == 200
    assert 'id="bound-sources"' in page.text
    # a searchable single-select combobox, not an always-open list of every source
    assert "source-combobox" in page.text
    assert 'class="source-results" hidden' in page.text   # collapsed until you search
    # the bound source shows as a chip; the unbound one is a pick option
    assert "source-chip" in page.text
    # the old auto-submitting checkbox behaviour is gone for existing controls
    assert 'onchange="this.form.submit()"' not in page.text


def test_add_source_via_picker_persists_without_redirect(client):
    _make_source(client, "users")
    _make_source(client, "other")
    _control(client, "users")
    r = client.post("/controls/c1/sources",
                    data={"action": "add", "source_id": "other"},
                    follow_redirects=False)
    assert r.status_code == 200            # HTMX fragment, NOT a 303 redirect
    assert "location" not in {k.lower() for k in r.headers}
    assert 'id="bound-sources"' in r.text
    assert "other" in r.text               # now bound → appears as a chip
    # confirm it stuck
    page = client.get("/controls/c1")
    assert page.text.count("source-chip") >= 2


def test_remove_source_via_picker(client):
    _make_source(client, "users")
    _make_source(client, "other")
    _control(client, "users")
    client.post("/controls/c1/sources", data={"action": "add", "source_id": "other"},
                follow_redirects=False)
    r = client.post("/controls/c1/sources",
                    data={"action": "remove", "source_id": "other"},
                    follow_redirects=False)
    assert r.status_code == 200
    # 'other' is back in the picker options, not a chip
    assert 'hx-vals=\'{"action": "add", "source_id": "other"}\'' in r.text


def test_source_new_uses_styled_tabs(client):
    page = client.get("/sources/new")
    assert page.status_code == 200
    assert 'class="tabs"' in page.text
    assert 'class="tabbar"' not in page.text
