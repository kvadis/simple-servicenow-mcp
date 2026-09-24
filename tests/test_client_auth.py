"""Authentication behaviour of ServiceNowClient, against a mocked transport.

`_auth_headers` and `_refresh_oauth_token` had no coverage at all before the
authorization-code work, so these tests also pin down the pre-existing
client-credentials path — the refactor that made room for a second grant type
would otherwise have been unguarded.

Every test drives a real public method (`list_records`) through the real retry
loop; only the socket is faked.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx
import pytest

from simple_servicenow_mcp.client import ServiceNowAPIError, ServiceNowClient
from simple_servicenow_mcp.config import Settings
from simple_servicenow_mcp.token_store import StoredToken, TokenStore, token_key

INSTANCE = "https://acmedev.service-now.com"
CLIENT_ID = "cid"


# ── harness ─────────────────────────────────────────────────────────


@dataclass
class Recorder:
    """Fakes /oauth_token.do and the Table API, recording what was asked."""

    token_bodies: list[dict] = field(default_factory=list)
    api_statuses: list[int] = field(default_factory=list)
    token_status: int = 200
    token_calls: list[httpx.Request] = field(default_factory=list)
    api_calls: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth_token.do":
            self.token_calls.append(request)
            i = min(len(self.token_calls) - 1, len(self.token_bodies) - 1)
            body = self.token_bodies[i] if self.token_bodies else {}
            return httpx.Response(self.token_status, json=body)
        self.api_calls.append(request)
        n = len(self.api_calls) - 1
        status = self.api_statuses[min(n, len(self.api_statuses) - 1)] if self.api_statuses else 200
        if status == 200:
            return httpx.Response(200, json={"result": [{"sys_id": "1"}]})
        return httpx.Response(status, json={"error": {"message": "nope"}})

    @property
    def token_forms(self) -> list[dict[str, str]]:
        out = []
        for r in self.token_calls:
            out.append(dict(httpx.QueryParams(r.content.decode())))
        return out

    @property
    def bearers(self) -> list[str]:
        return [r.headers.get("Authorization", "") for r in self.api_calls]


def _settings(**over) -> Settings:
    base = {
        "instance_url": INSTANCE,
        "auth_method": "oauth_authorization_code",
        "client_id": CLIENT_ID,
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def _client(rec: Recorder, settings: Settings, name: str = "acme-dev") -> ServiceNowClient:
    return ServiceNowClient(settings, instance_name=name, transport=httpx.MockTransport(rec))


def _stored(**over) -> StoredToken:
    base = {
        "access_token": "at-old",
        "refresh_token": "rt-old",
        "expires_at": time.time() + 1800,
        "instance_name": "acme-dev",
        "scope": "",
        "obtained_at": time.time(),
    }
    base.update(over)
    return StoredToken(**base)


@pytest.fixture
def store(tmp_path) -> TokenStore:
    return TokenStore(tmp_path / "tokens.json")


KEY = token_key(INSTANCE, CLIENT_ID)


# ── regression guard: client_credentials still works ────────────────


async def test_client_credentials_path_is_unchanged():
    rec = Recorder(token_bodies=[{"access_token": "at-cc", "expires_in": 1800}])
    c = _client(rec, _settings(auth_method="oauth", client_secret="secret"))
    await c.list_records("incident")
    assert rec.token_forms[0]["grant_type"] == "client_credentials"
    assert rec.token_forms[0]["client_secret"] == "secret"
    assert rec.bearers == ["Bearer at-cc"]
    await c.close()


async def test_client_credentials_token_is_reused_across_calls():
    rec = Recorder(token_bodies=[{"access_token": "at-cc", "expires_in": 1800}])
    c = _client(rec, _settings(auth_method="oauth", client_secret="s"))
    await c.list_records("incident")
    await c.list_records("problem")
    assert len(rec.token_calls) == 1, "a live token must not be re-minted"
    await c.close()


async def test_basic_auth_sends_a_basic_header_and_never_calls_the_token_endpoint():
    rec = Recorder()
    c = _client(rec, _settings(auth_method="basic", username="u", password="p"))
    await c.list_records("incident")
    assert rec.token_calls == []
    assert rec.bearers[0].startswith("Basic ")
    await c.close()


# ── authorization code: adopting a stored token ─────────────────────


async def test_stored_token_is_used_without_any_network_call(store):
    store.save(KEY, _stored(access_token="at-stored"))
    rec = Recorder()
    c = _client(rec, _settings(token_store=store.path))
    await c.list_records("incident")
    assert rec.token_calls == [], "a valid stored token needs no refresh — this is restart-survival"
    assert rec.bearers == ["Bearer at-stored"]
    await c.close()


async def test_missing_token_raises_an_actionable_error_naming_the_instance(store):
    rec = Recorder()
    c = _client(rec, _settings(token_store=store.path))
    with pytest.raises(ServiceNowAPIError) as ei:
        await c.list_records("incident")
    msg = str(ei.value)
    assert "acme-dev" in msg
    assert "run: simple-servicenow-mcp login --instance acme-dev" in msg
    await c.close()


async def test_token_written_after_startup_is_picked_up_without_a_restart(store):
    """`login` runs in a terminal while the server is already up."""
    rec = Recorder()
    c = _client(rec, _settings(token_store=store.path))
    with pytest.raises(ServiceNowAPIError):
        await c.list_records("incident")
    store.save(KEY, _stored(access_token="at-late"))
    await c.list_records("incident")
    assert rec.bearers[-1] == "Bearer at-late"
    await c.close()


# ── authorization code: refresh ─────────────────────────────────────


async def test_expired_token_triggers_a_refresh_grant(store):
    store.save(KEY, _stored(access_token="at-old", expires_at=time.time() - 1))
    rec = Recorder(token_bodies=[{"access_token": "at-new", "expires_in": 1800}])
    c = _client(rec, _settings(token_store=store.path))
    await c.list_records("incident")
    form = rec.token_forms[0]
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == "rt-old"
    assert form["client_id"] == CLIENT_ID
    assert rec.bearers == ["Bearer at-new"]
    await c.close()


async def test_public_client_omits_the_secret_on_refresh(store):
    store.save(KEY, _stored(expires_at=time.time() - 1))
    rec = Recorder(token_bodies=[{"access_token": "at-new", "expires_in": 1800}])
    c = _client(rec, _settings(token_store=store.path, client_secret=""))
    await c.list_records("incident")
    assert "client_secret" not in rec.token_forms[0]
    await c.close()


async def test_confidential_client_sends_the_secret_on_refresh(store):
    store.save(KEY, _stored(expires_at=time.time() - 1))
    rec = Recorder(token_bodies=[{"access_token": "at-new", "expires_in": 1800}])
    c = _client(rec, _settings(token_store=store.path, client_secret="shh"))
    await c.list_records("incident")
    assert rec.token_forms[0]["client_secret"] == "shh"
    await c.close()


async def test_rotated_refresh_token_is_persisted(store):
    store.save(KEY, _stored(expires_at=time.time() - 1))
    rec = Recorder(
        token_bodies=[{"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 1800}]
    )
    c = _client(rec, _settings(token_store=store.path))
    await c.list_records("incident")
    assert store.load(KEY).refresh_token == "rt-new"
    await c.close()


async def test_unrotated_refresh_token_is_kept(store):
    """Most releases return no refresh_token on refresh — we must not lose it."""
    store.save(KEY, _stored(expires_at=time.time() - 1))
    rec = Recorder(token_bodies=[{"access_token": "at-new", "expires_in": 1800}])
    c = _client(rec, _settings(token_store=store.path))
    await c.list_records("incident")
    assert store.load(KEY).refresh_token == "rt-old"
    await c.close()


async def test_refresh_preserves_the_instance_name(store):
    store.save(KEY, _stored(expires_at=time.time() - 1, instance_name="acme-dev"))
    rec = Recorder(token_bodies=[{"access_token": "at-new", "expires_in": 1800}])
    c = _client(rec, _settings(token_store=store.path))
    await c.list_records("incident")
    assert store.load(KEY).instance_name == "acme-dev"
    await c.close()


async def test_expiry_skew_of_60s_is_applied(store):
    store.save(KEY, _stored(expires_at=time.time() - 1))
    rec = Recorder(token_bodies=[{"access_token": "at-new", "expires_in": 120}])
    c = _client(rec, _settings(token_store=store.path))
    before = time.time()
    await c.list_records("incident")
    # 120s lifetime minus the 60s safety margin.
    assert before + 55 <= store.load(KEY).expires_at <= before + 61
    await c.close()


async def test_expired_grant_with_no_refresh_token_asks_for_a_new_login(store):
    store.save(KEY, _stored(expires_at=time.time() - 1, refresh_token=None))
    rec = Recorder()
    c = _client(rec, _settings(token_store=store.path))
    with pytest.raises(ServiceNowAPIError, match="login"):
        await c.list_records("incident")
    assert rec.token_calls == []
    await c.close()


async def test_invalid_grant_is_reported_as_needing_re_login(store):
    """ServiceNow puts the meaningful half in either field — we match on both."""
    store.save(KEY, _stored(expires_at=time.time() - 1))
    rec = Recorder(
        token_status=401,
        token_bodies=[{"error": "server_error", "error_description": "invalid_grant"}],
    )
    c = _client(rec, _settings(token_store=store.path))
    with pytest.raises(ServiceNowAPIError) as ei:
        await c.list_records("incident")
    assert "invalid_grant" in str(ei.value)
    assert "run: simple-servicenow-mcp login" in str(ei.value)
    await c.close()


# ── 401 handling ────────────────────────────────────────────────────


async def test_401_refreshes_and_retries_exactly_once(store):
    store.save(KEY, _stored(access_token="at-dead"))
    rec = Recorder(
        api_statuses=[401, 200],
        token_bodies=[{"access_token": "at-fresh", "expires_in": 1800}],
    )
    c = _client(rec, _settings(token_store=store.path))
    rows = await c.list_records("incident")
    assert rows == [{"sys_id": "1"}]
    assert len(rec.api_calls) == 2
    assert len(rec.token_calls) == 1
    assert rec.bearers == ["Bearer at-dead", "Bearer at-fresh"]
    await c.close()


async def test_401_twice_raises_after_a_single_refresh(store):
    store.save(KEY, _stored(access_token="at-dead"))
    rec = Recorder(
        api_statuses=[401],
        token_bodies=[{"access_token": "at-fresh", "expires_in": 1800}],
    )
    c = _client(rec, _settings(token_store=store.path))
    with pytest.raises(ServiceNowAPIError) as ei:
        await c.list_records("incident")
    assert ei.value.status == 401
    assert len(rec.token_calls) == 1, "one refresh attempt, not a loop"
    assert len(rec.api_calls) == 2
    await c.close()


async def test_auth_retry_survives_max_retries_zero(store):
    """max_retries=0 is legal; the re-auth must not be starved by it."""
    store.save(KEY, _stored(access_token="at-dead"))
    rec = Recorder(
        api_statuses=[401, 200],
        token_bodies=[{"access_token": "at-fresh", "expires_in": 1800}],
    )
    c = _client(rec, _settings(token_store=store.path, max_retries=0))
    assert await c.list_records("incident") == [{"sys_id": "1"}]
    assert len(rec.api_calls) == 2
    await c.close()


async def test_basic_auth_does_not_retry_on_401():
    """A wrong password should fail immediately, not spend attempts."""
    rec = Recorder(api_statuses=[401])
    c = _client(rec, _settings(auth_method="basic", username="u", password="bad"))
    with pytest.raises(ServiceNowAPIError):
        await c.list_records("incident")
    assert len(rec.api_calls) == 1
    await c.close()


async def test_403_is_not_treated_as_an_auth_problem(store):
    """Permission errors must not trigger a pointless refresh."""
    store.save(KEY, _stored())
    rec = Recorder(api_statuses=[403])
    c = _client(rec, _settings(token_store=store.path))
    with pytest.raises(ServiceNowAPIError) as ei:
        await c.list_records("incident")
    assert ei.value.status == 403
    assert rec.token_calls == []
    assert len(rec.api_calls) == 1
    await c.close()


# ── concurrency and cross-process behaviour ─────────────────────────


async def test_concurrent_calls_spend_only_one_grant(store):
    store.save(KEY, _stored(expires_at=time.time() - 1))
    rec = Recorder(token_bodies=[{"access_token": "at-new", "expires_in": 1800}])
    c = _client(rec, _settings(token_store=store.path))
    await asyncio.gather(*(c.list_records("incident") for _ in range(10)))
    assert len(rec.token_calls) == 1, "the refresh lock must collapse the stampede"
    assert len(rec.api_calls) == 10
    await c.close()


async def test_a_401_adopts_a_token_another_process_already_refreshed(store):
    """Two servers share one store; the other one already fixed the token."""
    store.save(KEY, _stored(access_token="at-dead"))
    rec = Recorder(api_statuses=[401, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        resp = rec(request)
        # The first API call has just been rejected — the peer process rotates
        # the shared token before we get as far as retrying.
        if request.url.path != "/oauth_token.do" and len(rec.api_calls) == 1:
            store.save(KEY, _stored(access_token="at-other-process"))
        return resp

    c = ServiceNowClient(
        _settings(token_store=store.path),
        instance_name="acme-dev",
        transport=httpx.MockTransport(handler),
    )
    await c.list_records("incident")
    assert rec.token_calls == [], "adopt the peer's token rather than spending the grant"
    assert rec.bearers[-1] == "Bearer at-other-process"
    await c.close()


async def test_two_instances_keep_separate_tokens(tmp_path):
    shared = TokenStore(tmp_path / "tokens.json")
    other = "https://dev12345.service-now.com"
    shared.save(token_key(INSTANCE, CLIENT_ID), _stored(access_token="at-acme"))
    shared.save(token_key(other, CLIENT_ID), _stored(access_token="at-pdi"))

    rec_a, rec_b = Recorder(), Recorder()
    a = _client(rec_a, _settings(token_store=shared.path), name="acme-dev")
    b = _client(rec_b, _settings(instance_url=other, token_store=shared.path), name="pdi")
    await a.list_records("incident")
    await b.list_records("incident")

    assert rec_a.bearers == ["Bearer at-acme"]
    assert rec_b.bearers == ["Bearer at-pdi"]
    await a.close()
    await b.close()


# ── retry budget ────────────────────────────────────────────────────


async def test_oauth_5xx_retry_count_is_unchanged_by_the_auth_slot():
    """The extra 401 slot must not silently widen ordinary 5xx retries.

    max_retries=3 means 4 attempts total for a persistent server error — the same
    for every auth method. Reserving a slot for re-auth must not spend it here.
    """
    rec = Recorder(
        api_statuses=[500],
        token_bodies=[{"access_token": "at", "expires_in": 1800}],
    )
    c = _client(
        rec,
        _settings(
            auth_method="oauth",
            client_secret="s",
            max_retries=3,
            retry_base_delay=0.0,
            retry_max_delay=1.0,
        ),
    )
    with pytest.raises(ServiceNowAPIError):
        await c.list_records("incident")
    assert len(rec.api_calls) == 4
    await c.close()


async def test_basic_5xx_retry_count_reference():
    rec = Recorder(api_statuses=[500])
    c = _client(
        rec,
        _settings(
            auth_method="basic",
            username="u",
            password="p",
            max_retries=3,
            retry_base_delay=0.0,
            retry_max_delay=1.0,
        ),
    )
    with pytest.raises(ServiceNowAPIError):
        await c.list_records("incident")
    assert len(rec.api_calls) == 4
    await c.close()


async def test_401_still_gets_its_extra_attempt_after_the_fix(store):
    """A 401 followed by persistent 5xx keeps the full post-auth retry budget."""
    store.save(KEY, _stored(access_token="at-dead"))
    rec = Recorder(
        api_statuses=[401, 500],
        token_bodies=[{"access_token": "at-fresh", "expires_in": 1800}],
    )
    c = _client(
        rec,
        _settings(token_store=store.path, max_retries=3, retry_base_delay=0.0, retry_max_delay=1.0),
    )
    with pytest.raises(ServiceNowAPIError):
        await c.list_records("incident")
    # 1 rejected call + 4 attempts carrying the refreshed token.
    assert len(rec.api_calls) == 5
    await c.close()
