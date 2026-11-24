"""Celery application configuration for EasyLV."""

import logging
import os

from celery import Celery
from celery.signals import worker_ready

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "easylv.settings")

app = Celery("easylv")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@worker_ready.connect
def announce_worker_ready(**kwargs) -> None:
    logging.getLogger("invoicing").info("worker ready")
