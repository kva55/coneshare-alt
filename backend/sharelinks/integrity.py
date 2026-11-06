"""Findings about how page requests were made (server side). Findings, not proof: a user agent is
self-declared, and a determined script can send whatever a browser sends. They make careless
automation visible; they do not stop a careful one."""
import re

AUTOMATION_UA = re.compile(
    r'headlesschrome|phantomjs|puppeteer|playwright|selenium|python-requests|python-urllib|aiohttp|httpx|'
    r'\bcurl/|\bwget/|go-http-client|node-fetch|axios/|okhttp|libwww|scrapy|java/', re.I)


def automation_marker(request) -> str:
    """The first automation marker found in the User-Agent, or ''."""
    found = AUTOMATION_UA.search(request.META.get('HTTP_USER_AGENT', '') or '')
    return found.group(0).lower() if found else ''


def monitoring_on() -> bool:
    from security.services import snapshot
    return bool(snapshot()['monitoring_enabled'])
