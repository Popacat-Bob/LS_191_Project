import hashlib
import hmac
import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import requests
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from urllib3.connection import HTTPConnection
from urllib3.connectionpool import HTTPConnectionPool

from integrations.models import WebhookEvent
from integrations.services import handle_facebook_webhook
from integrations.sources import (
    CommentRecord,
    GraphApiCommentSource,
    SimulatedCommentSource,
    get_comment_source,
)
from integrations.tasks import enqueue_source_comments, process_comment
from invoicing.tasks import send_invoice
from mining.models import Reservation
from tests.factories import InventoryItemFactory


def _payload(*comment_ids: str) -> bytes:
    timestamp = datetime(2026, 11, 10, 12, 0, tzinfo=UTC).isoformat()
    return json.dumps(
        {
            "comments": [
                {
                    "fb_comment_id": comment_id,
                    "live_video_id": "live-1",
                    "message": "mine 101",
                    "fb_commenter_id": f"buyer-{comment_id}",
                    "fb_commenter_name": "Test Buyer",
                    "created_time": timestamp,
                }
                for comment_id in comment_ids
            ]
        }
    ).encode()


def _signature(body: bytes, secret: str = "test-app-secret") -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.mark.parametrize(
    ("params", "verify_token", "expected_status", "expected_body"),
    [
        (
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "expected",
                "hub.challenge": "challenge-value",
            },
            "expected",
            200,
            "challenge-value",
        ),
        (
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "challenge-value",
            },
            "expected",
            403,
            "",
        ),
        (
            {
                "hub.mode": "unsubscribe",
                "hub.verify_token": "expected",
                "hub.challenge": "challenge-value",
            },
            "expected",
            403,
            "",
        ),
        (
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "expected",
                "hub.challenge": "challenge-value",
            },
            "",
            403,
            "",
        ),
    ],
)
def test_get_challenge_verification(
    params, verify_token, expected_status, expected_body
):
    with override_settings(FB_VERIFY_TOKEN=verify_token):
        response = Client().get(reverse("facebook-webhook"), params)

    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.content.decode() == expected_body
        assert response["Content-Type"].startswith("text/plain")
    else:
        assert response.data["error"]["code"] == "permission_denied"


@pytest.mark.django_db(transaction=True)
@override_settings(FB_APP_SECRET="test-app-secret")
def test_valid_signature_enqueues_each_comment_once():
    body = _payload("one", "two")
    client = Client(enforce_csrf_checks=True)
    with patch.object(process_comment, "delay") as enqueue:
        response = client.generic(
            "POST",
            reverse("facebook-webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=_signature(body),
        )

    assert response.status_code == 200
    assert WebhookEvent.objects.filter(source=WebhookEvent.Source.FACEBOOK).count() == 2
    assert enqueue.call_count == 2
    assert {call.kwargs["webhook_event_id"] for call in enqueue.call_args_list} == {
        "fb-comment:one",
        "fb-comment:two",
    }


@pytest.mark.django_db
@override_settings(FB_APP_SECRET="test-app-secret")
def test_signed_empty_payload_returns_ok_without_events_or_enqueues():
    body = _payload()
    with patch.object(process_comment, "delay") as enqueue:
        response = Client().generic(
            "POST",
            reverse("facebook-webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=_signature(body),
        )

    assert response.status_code == 200
    assert WebhookEvent.objects.count() == 0
    enqueue.assert_not_called()


@pytest.mark.django_db(transaction=True)
@override_settings(FB_APP_SECRET="test-app-secret")
def test_duplicate_delivery_enqueues_and_records_once_and_task_marks_processed():
    body = _payload("duplicate")
    signature = _signature(body)
    client = Client()
    with patch.object(process_comment, "delay") as enqueue:
        first = client.generic(
            "POST",
            reverse("facebook-webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
        )
        queued_data = enqueue.call_args.args[0]
        queued_event_id = enqueue.call_args.kwargs["webhook_event_id"]
        second = client.generic(
            "POST",
            reverse("facebook-webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
        )

    assert first.status_code == second.status_code == 200
    assert enqueue.call_count == 1
    assert WebhookEvent.objects.filter(event_id="fb-comment:duplicate").count() == 1
    InventoryItemFactory(mining_code="101", stock_qty=1)
    with patch.object(send_invoice, "delay"):
        task_result = process_comment.apply(
            args=(queued_data,),
            kwargs={"webhook_event_id": queued_event_id},
            throw=True,
        ).get(propagate=True)
    event = WebhookEvent.objects.get(event_id=queued_event_id)
    assert task_result["outcome"] == "RESERVED"
    assert event.processed_at is not None


@pytest.mark.django_db
@override_settings(FB_APP_SECRET="test-app-secret")
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"HTTP_X_HUB_SIGNATURE_256": "sha256=bad"},
        {"HTTP_X_HUB_SIGNATURE_256": "not-a-signature"},
    ],
)
def test_bad_or_missing_signature_is_forbidden_without_side_effects(headers):
    body = _payload("rejected")
    with patch.object(process_comment, "delay") as enqueue:
        response = Client().generic(
            "POST",
            reverse("facebook-webhook"),
            data=body,
            content_type="application/json",
            **headers,
        )

    assert response.status_code == 403
    assert WebhookEvent.objects.count() == 0
    enqueue.assert_not_called()


@pytest.mark.django_db
def test_unset_secret_rejects_even_valid_looking_signature():
    body = _payload("secret-unset")
    with (
        override_settings(FB_APP_SECRET=""),
        patch.object(process_comment, "delay") as enqueue,
    ):
        response = Client().generic(
            "POST",
            reverse("facebook-webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=_signature(body),
        )

    assert response.status_code == 403
    assert WebhookEvent.objects.count() == 0
    enqueue.assert_not_called()


@pytest.mark.django_db
@override_settings(FB_APP_SECRET="test-app-secret")
def test_tampered_body_is_forbidden():
    body = _payload("original")
    tampered_body = _payload("tampered")
    with patch.object(process_comment, "delay") as enqueue:
        response = Client().generic(
            "POST",
            reverse("facebook-webhook"),
            data=tampered_body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=_signature(body),
        )

    assert response.status_code == 403
    assert WebhookEvent.objects.count() == 0
    enqueue.assert_not_called()


@pytest.mark.django_db
@override_settings(FB_APP_SECRET="test-app-secret")
def test_malformed_json_with_valid_signature_is_bad_request():
    body = b'{"comments":'
    response = Client().generic(
        "POST",
        reverse("facebook-webhook"),
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=_signature(body),
    )

    assert response.status_code == 400
    assert WebhookEvent.objects.count() == 0


@pytest.mark.django_db
@override_settings(FB_APP_SECRET="test-app-secret")
def test_invalid_signature_is_rejected_before_malformed_json_parsing():
    body = b'{"comments":'
    response = Client().generic(
        "POST",
        reverse("facebook-webhook"),
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=_signature(b"different-body"),
    )

    assert response.status_code == 403
    assert WebhookEvent.objects.count() == 0


@pytest.mark.django_db
@override_settings(FB_APP_SECRET="test-app-secret")
def test_webhook_signature_comparison_uses_constant_time_compare():
    body = _payload()
    signature = _signature(body)
    with patch(
        "integrations.services.hmac.compare_digest", wraps=hmac.compare_digest
    ) as compare:
        assert handle_facebook_webhook(body, signature) == 0

    compare.assert_called_once()


def test_graph_api_source_methods_are_deferred():
    with pytest.raises(NotImplementedError, match="Graph API is deferred"):
        GraphApiCommentSource().fetch_comments()


@pytest.mark.parametrize("source_name", ["simulated", "graph_api"])
def test_comment_source_factory_selects_configured_source(source_name):
    with override_settings(COMMENT_SOURCE=source_name):
        source = get_comment_source()
    assert isinstance(
        source,
        SimulatedCommentSource if source_name == "simulated" else GraphApiCommentSource,
    )


def test_comment_source_factory_rejects_unknown_selector():
    with override_settings(COMMENT_SOURCE="unknown"):
        with pytest.raises(ImproperlyConfigured, match="Unknown COMMENT_SOURCE"):
            get_comment_source()


def test_simulated_source_queues_and_filters_comment_records():
    source = SimulatedCommentSource()
    record = CommentRecord(
        fb_comment_id="queued",
        live_video_id="live-a",
        message="mine 101",
        fb_commenter_id="buyer",
        fb_commenter_name="Buyer",
        created_time=timezone.now(),
    )
    source.publish(record)

    assert list(source.fetch_comments("other-live")) == []
    assert list(source.fetch_comments("live-a")) == [record]


def test_source_dispatch_uses_the_shared_celery_task():
    source = SimulatedCommentSource()
    source.publish(
        CommentRecord(
            fb_comment_id="dispatched",
            live_video_id="live-a",
            message="mine 101",
            fb_commenter_id="buyer",
            fb_commenter_name="Buyer",
            created_time=timezone.now(),
        )
    )
    with patch("integrations.tasks.process_comment.delay") as enqueue:
        assert enqueue_source_comments(source, "live-a") == 1

    enqueue.assert_called_once()
    assert enqueue.call_args.args[0]["fb_comment_id"] == "dispatched"
    assert isinstance(enqueue.call_args.args[0]["created_time"], str)


@pytest.mark.django_db(transaction=True)
def test_process_comment_task_rebuilds_iso_datetime_and_runs_mining_flow():
    InventoryItemFactory(mining_code="101", stock_qty=1)
    body = json.loads(_payload("task").decode())
    with patch.object(send_invoice, "delay"):
        task_result = process_comment.apply(
            args=(body["comments"][0],), throw=True
        ).get()

    assert task_result["outcome"] == "RESERVED"
    assert Reservation.objects.count() == 1


def test_autouse_network_guard_blocks_external_requests_and_urllib3_connections():
    with pytest.raises(AssertionError, match="External network access"):
        requests.get("https://non-local.invalid/")
    with pytest.raises(AssertionError, match="External network access"):
        HTTPConnectionPool("non-local.invalid", 80)._new_conn()
    with pytest.raises(AssertionError, match="External network access"):
        HTTPConnection("non-local.invalid")._new_conn()
