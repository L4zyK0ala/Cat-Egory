"""Community reputation: a trusted account's category corrections apply at once; nothing else changes."""
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.config import get_settings
from database.models import ApiKey, AuditLog, CommunitySubmission, ModerationStatus, SubmissionType
from tests.integration import test_community
from tests.integration.test_community import report, state

site = pytest.fixture(test_community.site.__wrapped__)


def history(engine, key_id, accepted, rejected=0, spam=0, moderator=None):
    """Moderator decisions on earlier submissions of the key's account."""
    with Session(engine) as s, s.begin():
        user_id = s.get(ApiKey, key_id).user_id
        for status, n in ((ModerationStatus.ACCEPTED, accepted), (ModerationStatus.REJECTED, rejected),
                          (ModerationStatus.SPAM, spam)):
            for _ in range(n):
                s.add(CommunitySubmission(type=SubmissionType.NEW_DOMAIN, submitted_domain="old.example.com",
                                          submitter_id=user_id, status=status, moderator_id=moderator or user_id))


@pytest.fixture
def trusted(engine, make_key, admin):
    headers, key_id = make_key("contributor", scopes=["community:write"])
    with Session(engine) as s:
        moderator = s.scalar(select(ApiKey.user_id).where(ApiKey.id != key_id).order_by(ApiKey.id))
    history(engine, key_id, accepted=get_settings().community_trust_min_accepted, moderator=moderator)
    return headers


def test_a_trusted_correction_applies_at_once(engine, client, site, trusted):
    r = report(client, trusted)
    assert r.json()["status"] == "accepted"
    assert state(engine)[:2] == ("gambling", ["entertainment"])  # "sports" removed
    with Session(engine) as s:
        sub = s.scalar(select(CommunitySubmission).where(CommunitySubmission.submitted_domain == site))
        assert sub.moderator_id is None and sub.moderation_note.startswith("applied at once")
        assert s.scalar(select(AuditLog.action).where(AuditLog.action == "community.auto_accept"))


def test_threat_reports_always_wait(engine, client, site, trusted):
    r = report(client, trusted, type="false_negative", category="phishing")
    assert r.json()["status"] == "pending"


def test_a_main_category_change_without_a_new_one_waits(engine, client, site, trusted):
    assert report(client, trusted, category="gambling").json()["status"] == "pending"


def test_untrusted_accounts_and_visitors_wait(engine, client, site, make_key):
    headers, key_id = make_key("contributor", scopes=["community:write"])
    history(engine, key_id, accepted=get_settings().community_trust_min_accepted, rejected=3)  # below 90%
    assert report(client, headers).json()["status"] == "pending"
    assert state(engine)[:2] == ("gambling", ["sports", "entertainment"])


def test_anonymous_visitors_always_wait(engine, client, site):
    assert report(client).json()["status"] == "pending"
    assert state(engine)[:2] == ("gambling", ["sports", "entertainment"])


def test_spam_ever_means_no_trust(engine, client, site, make_key):
    headers, key_id = make_key("contributor", scopes=["community:write"])
    history(engine, key_id, accepted=50, spam=1)
    assert report(client, headers).json()["status"] == "pending"


def test_automatic_acceptances_do_not_build_trust(engine, client, site, trusted):
    from backend.app.api.community import reputation

    report(client, trusted)
    with Session(engine) as s:
        user_id = s.scalar(select(CommunitySubmission.submitter_id).where(CommunitySubmission.moderator_id.is_(None)))
        assert reputation(s, user_id).accepted == get_settings().community_trust_min_accepted


def test_moderators_see_the_reputation(engine, client, site, make_key, admin):
    headers, key_id = make_key("contributor", scopes=["community:write"])
    history(engine, key_id, accepted=2, rejected=1)
    report(client, headers)
    item = client.get("/api/v1/admin/submissions", headers=admin).json()[0]
    assert item["submitter"] == {"account": True, "accepted": 2, "rejected": 1, "spam": 0, "trusted": False}
