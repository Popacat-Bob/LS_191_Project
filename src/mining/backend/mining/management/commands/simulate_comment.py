"""Simulate individual mines or a race for a demo item."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, connections
from django.utils import timezone

from integrations.tasks import process_comment
from inventory.models import InventoryItem
from inventory.services import find_item, manage_inventory
from mining.models import LiveComment
from mining.services import CommentData
from orders.models import Order


class Command(BaseCommand):
    """Run an offline mining simulation."""

    help = "Simulate one mine or run a multi-commenter race for a demo item."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--code", default="DEMO-101")
        parser.add_argument("--user", default="Juan")
        parser.add_argument("--ts", help="Original comment timestamp in ISO format.")
        parser.add_argument("--video", default="simulated-live")
        parser.add_argument("--comment-id")
        parser.add_argument("--scenario", choices=["race"])
        parser.add_argument(
            "--no-prepare",
            action="store_true",
            help="Process a comment without creating or increasing item stock.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["scenario"] == "race":
            self._run_race(options["code"])
            return
        self._run_one(
            code=options["code"],
            name=options["user"],
            timestamp=self._timestamp(options["ts"]),
            video=options["video"],
            comment_id=options["comment_id"] or f"simulated-{uuid4()}",
            prepare_item=not options["no_prepare"],
        )

    def _timestamp(self, value: str | None) -> datetime:
        if value is None:
            return timezone.now()
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise CommandError("--ts must be an ISO-formatted timestamp.") from exc

    def _seed_item(self, code: str) -> InventoryItem:
        item = find_item(code)
        if item is None:
            return manage_inventory(
                action="create",
                mining_code=code,
                name=f"Demo item {code}",
                description="Created by simulate_comment.",
                selling_price=Decimal("100.00"),
                acquisition_cost=Decimal("40.00"),
                stock_qty=1,
                reason="Seeded by the simulate_comment management command.",
            )
        if item.stock_qty == item.reserved_qty:
            return manage_inventory(
                action="adjust_stock",
                item=item,
                quantity_delta=1,
                reason="Restored one demo unit for a repeat race simulation.",
            )
        return item

    def _record_data(
        self,
        *,
        code: str,
        name: str,
        timestamp: datetime,
        video: str,
        comment_id: str,
        commenter_id: str,
    ) -> CommentData:
        return {
            "fb_comment_id": comment_id,
            "live_video_id": video,
            "message": f"mine {code}",
            "fb_commenter_id": commenter_id,
            "fb_commenter_name": name,
            "created_time": timestamp,
        }

    def _run_one(
        self,
        *,
        code: str,
        name: str,
        timestamp: datetime,
        video: str,
        comment_id: str,
        prepare_item: bool = True,
    ) -> None:
        if prepare_item:
            self._seed_item(code)
        elif find_item(code) is None:
            raise CommandError(
                f"Item {code!r} does not exist; --no-prepare will not create it."
            )
        data = self._record_data(
            code=code,
            name=name,
            timestamp=timestamp,
            video=video,
            comment_id=comment_id,
            commenter_id=f"simulate-{name.casefold()}",
        )
        result = process_comment.apply(
            args=(self._task_payload(data),), throw=True
        ).get(propagate=True)
        self.stdout.write(f"{name}: {result['outcome']} ({comment_id})")

    def _run_race(self, code: str) -> None:
        item = self._seed_item(code)
        base_time = timezone.now()
        run_id = uuid4().hex[:8]
        commenters = [
            ("Juan", f"sim-{run_id}-juan"),
            ("Maria", f"sim-{run_id}-maria"),
            ("Ana", f"sim-{run_id}-ana"),
            ("Luis", f"sim-{run_id}-luis"),
        ]
        records = [
            self._record_data(
                code=code,
                name=name,
                timestamp=base_time + timedelta(milliseconds=index),
                video="simulated-race",
                comment_id=f"sim-{run_id}-{index}",
                commenter_id=commenter_id,
            )
            for index, (name, commenter_id) in enumerate(commenters)
        ]

        def process_record(data: CommentData) -> dict[str, Any]:
            close_old_connections()
            try:
                return process_comment.apply(
                    args=(self._task_payload(data),), throw=True
                ).get(propagate=True)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(records)) as executor:
            results = list(executor.map(process_record, records))

        self.stdout.write("Commenter | Outcome | Comment ID")
        self.stdout.write("-" * 58)
        for (name, _), result in zip(commenters, results, strict=True):
            self.stdout.write(
                f"{name:<9} | {result['outcome']:<12} | {result['comment_id']}"
            )

        winner = next(
            (
                result
                for result in results
                if result["outcome"] == LiveComment.Outcome.RESERVED
            ),
            None,
        )
        total = Decimal("0.00")
        if winner is not None:
            reservation = item.reservations.get(pk=winner["reservation_id"])
            order = Order.objects.filter(
                customer=reservation.customer,
                status=Order.Status.UNPAID,
            ).first()
            if order is not None:
                total = order.total_amount
        self.stdout.write(f"Order total: {total}")

    @staticmethod
    def _task_payload(data: CommentData) -> dict[str, str]:
        """Serialize comment data for the Celery task."""
        return {
            **data,
            "created_time": data["created_time"].isoformat(),
        }
