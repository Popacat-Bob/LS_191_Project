from fastapi import FastAPI, Header, HTTPException

from facebook_graph_api import (
    FacebookAccessTokenError,
    FacebookGraphAPI,
    FacebookGraphAPIError,
)
from facebook_live_comments import CommentSummary, FacebookLiveCommentsService

app = FastAPI()
live_comments_service = FacebookLiveCommentsService(FacebookGraphAPI())


@app.get("/")
async def check_status():
    return {"status": "running", "service": "main backend"}


@app.get(
    "/facebook/live/{live_video_id}/my-comments",
    response_model=list[CommentSummary],
    summary="Get the caller's comments on a Facebook Live video",
    description="Requires a Facebook user access token with permission to read comments on the specified live video.",
)
async def get_my_live_comments(
    live_video_id: str,
    authorization: str | None = Header(default=None),
) -> list[CommentSummary]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="A Facebook bearer access token is required")
    access_token = authorization.removeprefix("Bearer ").strip()
    if not access_token:
        raise HTTPException(status_code=401, detail="A Facebook bearer access token is required")

    try:
        return await live_comments_service.get_my_comments(live_video_id, access_token)
    except FacebookAccessTokenError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    except FacebookGraphAPIError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
