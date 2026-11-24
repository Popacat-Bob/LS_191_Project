"""Webhook URL routes."""

from django.urls import path

from integrations.views import facebook_webhook

urlpatterns = [
    path("facebook/", facebook_webhook, name="facebook-webhook"),
]
