import asyncio
from typing import Any, Protocol
from urllib.parse import quote

from pydantic import BaseModel

from facebook_graph_api import FacebookGraphAPIError


class FacebookGraphClient(Protocol):
    def get(self, path: str, access_token: str, params: dict[str, str]) -> dict[str, Any]: ...


class CommentSummary(BaseModel):
    fb_name: str
    timestamp: str


class FacebookLiveCommentsService:
    def __init__(self, graph_api: FacebookGraphClient) -> None:
        self.graph_api = graph_api

    async def get_my_comments(
        self, live_video_id: str, access_token: str
    ) -> list[CommentSummary]:
        user = await asyncio.to_thread(
            self.graph_api.get, "me", access_token, {"fields": "id"}
        )
        user_id = user.get("id")
        if not isinstance(user_id, str) or not user_id:
            raise FacebookGraphAPIError("Facebook Graph API did not return the caller's ID")

        comments: list[CommentSummary] = []
        after: str | None = None
        seen_cursors: set[str] = set()

        while True:
            params = {"fields": "from{id,name},created_time", "limit": "100"}
            if after:
                params["after"] = after
            page = await asyncio.to_thread(
                self.graph_api.get,
                f"{quote(live_video_id, safe='')}/comments",
                access_token,
                params,
            )
            data = page.get("data")
            if not isinstance(data, list):
                raise FacebookGraphAPIError("Facebook Graph API returned invalid comments")

            comments.extend(self._matching_comments(data, user_id))
            after = self._next_cursor(page, seen_cursors)
            if after is None:
                break

        return comments

    @staticmethod
    def _matching_comments(data: list[Any], user_id: str) -> list[CommentSummary]:
        comments = []
        for comment in data:
            if not isinstance(comment, dict):
                continue
            author = comment.get("from")
            name = (
                author.get("name")
                if isinstance(author, dict) and author.get("id") == user_id
                else None
            )
            timestamp = comment.get("created_time")
            if isinstance(name, str) and isinstance(timestamp, str):
                comments.append(CommentSummary(fb_name=name, timestamp=timestamp))
        return comments

    @staticmethod
    def _next_cursor(page: dict[str, Any], seen_cursors: set[str]) -> str | None:
        paging = page.get("paging")
        cursors = paging.get("cursors") if isinstance(paging, dict) else None
        after = cursors.get("after") if isinstance(cursors, dict) else None
        if not isinstance(after, str) or not after or after in seen_cursors:
            return None
        seen_cursors.add(after)
        return after
