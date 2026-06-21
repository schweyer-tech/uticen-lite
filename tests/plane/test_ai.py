"""Route tests for AI-assisted authoring.

All provider calls go through a monkeypatched fake backend, so the suite never
makes a network call and passes with the ``[ai]`` SDKs absent. Offline-by-default
is asserted: with no provider configured (or its env absent) the draft endpoint
returns a friendly 200 partial and never constructs a backend.
"""

from __future__ import annotations

import io

from controlflow_sdk.store import repo
from controlflow_sdk.store.db import connect


def _make_source(client, sid="payments"):
    csv = b"payment_id,amount,approved_by\nP1,1000,\nP2,50,alice\n"
    client.post(
        "/sources",
        data={"source_id": sid, "format": "csv"},
        files={"file": (f"{sid}.csv", io.BytesIO(csv), "text/csv")},
        follow_redirects=False,
    )
    # Type `amount` as a number so a drafted `amount > 100` rule runs on the
    # sample (the AI gate coerces by declared data_type, like the real run).
    # `include__<col>` presence keeps the column included (form semantics).
    client.post(
        f"/sources/{sid}",
        data={
            "key_columns": "payment_id",
            "display_name__payment_id": "Payment ID", "data_type__payment_id": "text",
            "include__payment_id": "1",
            "display_name__amount": "Amount", "data_type__amount": "number",
            "include__amount": "1",
            "display_name__approved_by": "Approved By", "data_type__approved_by": "text",
            "include__approved_by": "1",
        },
        follow_redirects=False,
    )


def _configure_ai(client, provider="anthropic", model="claude-opus-4-8"):
    resp = client.post(
        "/settings/ai", data={"provider": provider, "model": model},
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302, 303)


def _patch_fake_backend(monkeypatch, spec):
    """Patch the orchestrator's provider factory to a fake returning *spec*."""

    class _Fake:
        def draft_rule_spec(self, objective, source_schema, data_sample, *, model):
            return spec

    monkeypatch.setattr(
        "controlflow_sdk.ai.draft.get_provider", lambda provider: _Fake()
    )


# --------------------------------------------------------------------------- #
# Offline-by-default guards
# --------------------------------------------------------------------------- #
def test_draft_no_provider_configured_returns_partial(client, monkeypatch):
    _make_source(client)
    # No provider saved → friendly partial, 200, no exception, no backend call.
    monkeypatch.setattr(
        "controlflow_sdk.ai.draft.get_provider",
        lambda provider: (_ for _ in ()).throw(AssertionError("must not call a backend")),
    )
    resp = client.post(
        "/controls/ai/draft",
        data={"objective": "Flag big payments", "source_ids": ["payments"]},
    )
    assert resp.status_code == 200
    assert "not configured" in resp.text.lower()


def test_draft_env_absent_returns_partial(client, monkeypatch):
    _make_source(client)
    _configure_ai(client, provider="anthropic", model="claude-opus-4-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "controlflow_sdk.ai.draft.get_provider",
        lambda provider: (_ for _ in ()).throw(AssertionError("must not call a backend")),
    )
    resp = client.post(
        "/controls/ai/draft",
        data={"objective": "Flag big payments", "source_ids": ["payments"]},
    )
    assert resp.status_code == 200
    assert "not enabled" in resp.text.lower()


# --------------------------------------------------------------------------- #
# Happy path + bad-draft path
# --------------------------------------------------------------------------- #
def test_draft_success_renders_prefilled_builder(client, monkeypatch):
    _make_source(client)
    _configure_ai(client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    _patch_fake_backend(monkeypatch, {
        "logic": "all",
        "severity": "high",
        "conditions": [
            {"column": "amount", "op": "gt", "value": 100},
            {"column": "approved_by", "op": "is_empty"},
        ],
    })
    resp = client.post(
        "/controls/ai/draft",
        data={"objective": "Flag large unapproved payments", "source_ids": ["payments"]},
    )
    assert resp.status_code == 200
    # The rule-builder markup comes back prefilled with the drafted column.
    assert 'name="cond_column"' in resp.text
    assert "amount" in resp.text  # a real source column appears in the dropdown selection


def test_draft_bad_dict_returns_error_partial_and_saves_nothing(client, monkeypatch):
    _make_source(client)
    _configure_ai(client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    _patch_fake_backend(monkeypatch, {
        "logic": "all", "conditions": [{"column": "amount", "op": "not_a_real_op"}],
    })
    resp = client.post(
        "/controls/ai/draft",
        data={"objective": "anything", "source_ids": ["payments"]},
    )
    assert resp.status_code == 200
    # An error partial, not builder markup; and absolutely no control row was created.
    conn = connect(client.app.state.project_root)
    controls = repo.list_controls(conn)
    conn.close()
    assert controls == []


def test_draft_no_source_bound_returns_error_partial(client, monkeypatch):
    _configure_ai(client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        "controlflow_sdk.ai.draft.get_provider",
        lambda provider: (_ for _ in ()).throw(AssertionError("must not call a backend")),
    )
    resp = client.post("/controls/ai/draft", data={"objective": "x", "source_ids": []})
    assert resp.status_code == 200
    assert "source" in resp.text.lower()


# --------------------------------------------------------------------------- #
# Settings panel
# --------------------------------------------------------------------------- #
def test_settings_get_lists_providers(client):
    resp = client.get("/settings/ai")
    assert resp.status_code == 200
    assert "Anthropic" in resp.text
    assert "OpenAI" in resp.text
    assert "Ollama" in resp.text


def test_settings_names_exact_env_vars_per_provider(client, monkeypatch):
    # U6: each cloud provider's hint names its real env var; Ollama says no key.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    text = client.get("/settings/ai").text
    assert "ANTHROPIC_API_KEY" in text
    assert "OPENAI_API_KEY" in text
    # Ollama needs no key — the page must say so rather than naming a key var.
    assert "no API key needed" in text
    # The generic, env-var-less wording is gone.
    assert "set the provider's environment variable" not in text


def test_settings_shows_enable_state_for_all_three(client, monkeypatch):
    # U6: enable/disable state is shown consistently for every provider, not just
    # Anthropic. With one cloud key present and one absent, we see both states,
    # plus Ollama always-enabled.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    text = client.get("/settings/ai").text
    # Two "enabled" badges (Anthropic + Ollama) and one "disabled" (OpenAI).
    assert text.count(">enabled<") == 2
    assert text.count(">disabled<") == 1


def test_settings_post_persists_selection(client):
    _configure_ai(client, provider="openai", model="gpt-4o")
    conn = connect(client.app.state.project_root)
    proj = repo.get_project(conn)
    conn.close()
    assert proj["system"]["ai"] == {"provider": "openai", "model": "gpt-4o"}


# --------------------------------------------------------------------------- #
# Editor affordance gating  (Logic ▸ Builder tab, not the Definition page)
# --------------------------------------------------------------------------- #
def _make_control(client, source_id="payments") -> str:
    """Create a minimal control bound to *source_id* and return its id."""
    resp = client.post(
        "/controls",
        data={"id": "AI-01", "title": "AI test ctrl", "objective": "Flag big payments",
              "narrative": "n", "source_ids": source_id},
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302, 303)
    return "AI-01"


def test_editor_hides_draft_when_not_configured(client):
    _make_source(client)
    cid = _make_control(client)
    page = client.get(f"/controls/{cid}/logic/builder").text
    # Not configured → the affordance links to settings rather than posting a draft.
    assert 'href="/settings/ai"' in page
    assert 'hx-post="/controls/ai/draft"' not in page


def test_editor_shows_draft_when_configured(client, monkeypatch):
    _make_source(client)
    cid = _make_control(client)
    _configure_ai(client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    page = client.get(f"/controls/{cid}/logic/builder").text
    assert 'hx-post="/controls/ai/draft"' in page
