import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class FacebookGraphAPIError(Exception):
    pass


class FacebookAccessTokenError(FacebookGraphAPIError):
    pass


class FacebookGraphAPI:
    def __init__(self, version: str | None = None, timeout: float = 15) -> None:
        self.version = version or os.getenv("FACEBOOK_GRAPH_API_VERSION", "v23.0")
        self.timeout = timeout

    def get(self, path: str, access_token: str, params: dict[str, str]) -> dict[str, Any]:
        query = urlencode(params)
        url = f"https://graph.facebook.com/{self.version}/{path}?{query}"
        request = Request(url, headers={"Authorization": f"Bearer {access_token}"})

        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read())
        except HTTPError as error:
            try:
                error_body = json.loads(error.read())
            except (json.JSONDecodeError, UnicodeDecodeError):
                error_body = {}
            self._raise_api_error(error_body, error)
        except (URLError, TimeoutError) as error:
            raise FacebookGraphAPIError("Could not reach Facebook Graph API") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise FacebookGraphAPIError("Facebook Graph API returned invalid JSON") from error

        if not isinstance(result, dict):
            raise FacebookGraphAPIError("Facebook Graph API returned an invalid response")
        if "error" in result:
            self._raise_api_error(result)
        return result

    @staticmethod
    def _raise_api_error(error_body: Any, cause: Exception | None = None) -> None:
        graph_error = error_body.get("error", {}) if isinstance(error_body, dict) else {}
        if isinstance(graph_error, dict) and graph_error.get("code") == 190:
            raise FacebookAccessTokenError("Facebook access token is invalid or expired") from cause
        raise FacebookGraphAPIError("Facebook Graph API request failed") from cause
