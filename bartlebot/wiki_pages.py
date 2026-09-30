"""References to Wizard101 Central pages, and turning them back into URLs.

A bot records where its drops came from so the table can be refreshed later. What
it records is a **page title** — ``Creature:Lost Soul`` — never a URL. The
scheme and host come from :data:`WIKI_HOST` in this file, so a bot file cannot
express a different host at all.

That is deliberate. Bot files are traded between users, and a URL stored in one
would be attacker-controlled input pointed at a network fetch, or worse at
something a user might click while trusting the app's framing of it. Storing a
title removes the class rather than mitigating it: there is no host to validate,
no lookalike domain to catch, no userinfo trick to parse around. The remaining
check is that the title cannot escape the ``/wiki/`` path, which is a small closed
rule instead of URL allowlisting.

:func:`resolve_wiki_page` is the single chokepoint. Nothing should read a
``wiki_page`` param directly or build a wiki URL by hand — go through here, and a
malformed reference resolves to None instead of a request.
"""

from __future__ import annotations

import logging
from urllib.parse import quote, unquote, urlparse

logger = logging.getLogger(__name__)

#: The only host any of this will ever produce or accept.
WIKI_HOST = "wiki.wizard101central.com"

_WIKI_PATH_PREFIX = "/wiki/"

#: Characters that would let a title escape the /wiki/ path or change the request.
#: Wiki titles legitimately contain spaces, colons, apostrophes and parentheses, so
#: only the structural ones are refused. A title containing a slash is rejected
#: rather than escaped — they exist on the wiki but no drop source has ever used one.
_FORBIDDEN_IN_TITLE = set('/\\?#<>[]{}|^"')

#: Namespaces that are really URL schemes. A title is only ever pasted into the
#: path of an https URL built here, so one of these resolves to a harmless 404 on
#: our own host rather than anything dangerous. Refused anyway: the moment some
#: future caller puts a raw title into an href, "javascript:alert(1)" stops being
#: a 404, and this is the cheap place to make that impossible.
_SCHEME_LIKE_PREFIXES = ("javascript:", "data:", "vbscript:", "file:", "http:", "https:", "about:")

#: The param key a node uses to record its source page.
WIKI_PAGE_PARAM = "wiki_page"


def wiki_page_path(url: str) -> str | None:
    """The ``/wiki/...`` path *url* points to on the wiki, or None if it does not.

    Relative hrefs are accepted as-is; an absolute URL must be https and its host
    must be exactly :data:`WIKI_HOST`.

    The host is compared against the parsed hostname rather than searched for in
    the raw string. A substring test accepts every one of these, all of which
    resolve to an attacker's server:

        https://wiki.wizard101central.com.evil.co/wiki/Creature:X
        https://evil.co/wiki/?ref=wiki.wizard101central.com
        https://evil.co/wiki.wizard101central.com/wiki/X
        https://wiki.wizard101central.com@evil.co/wiki/X

    Userinfo is rejected outright. The last form does reach the real wiki once the
    hostname is what is compared, but a URL that *reads* as one host and resolves
    to another has no legitimate use here.
    """
    url = url.strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        # A relative href from a fetched page — same-origin by construction.
        path = "/" + url.lstrip("/")
        return path if path.startswith(_WIKI_PATH_PREFIX) else None

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return None
    if parsed.username or parsed.password:
        return None
    if (parsed.hostname or "").lower() != WIKI_HOST:
        return None
    return parsed.path if parsed.path.startswith(_WIKI_PATH_PREFIX) else None


def is_valid_title(title: str) -> bool:
    """Whether *title* is a page title that cannot escape the ``/wiki/`` path."""
    title = title.strip()
    if not title or title.startswith("."):
        return False
    if ".." in title:
        return False
    if title.lower().startswith(_SCHEME_LIKE_PREFIXES):
        return False
    if any(ch in _FORBIDDEN_IN_TITLE for ch in title):
        return False
    return all(ord(ch) >= 0x20 and ord(ch) != 0x7F for ch in title)


def page_title(value: str) -> str | None:
    """Normalize *value* to a bare page title, or None if it is not usable.

    Accepts either a title already (``Creature:Lost Soul``) or a full wiki
    URL, so a user can paste whatever the browser gave them and the stored form is
    the same either way.
    """
    value = (value or "").strip()
    if not value:
        return None
    if "://" in value or value.lower().startswith(("http:", "https:")):
        path = wiki_page_path(value)
        if path is None:
            return None
        value = unquote(path[len(_WIKI_PATH_PREFIX) :]).replace("_", " ").strip()
    return value if is_valid_title(value) else None


def page_url(title: str) -> str | None:
    """The URL for a page title, or None when the title is not usable.

    The host is this module's constant, never anything derived from *title*.
    """
    if not is_valid_title(title):
        return None
    return f"https://{WIKI_HOST}{_WIKI_PATH_PREFIX}{quote(title.strip(), safe=':()')}"


def resolve_wiki_page(params: dict, key: str = WIKI_PAGE_PARAM) -> str | None:
    """The URL a node's recorded source page resolves to, or None.

    The one place a ``wiki_page`` param becomes a URL. Callers must not read the
    param themselves — going through here is what guarantees a reference out of an
    untrusted bot file cannot name a host of its own.
    """
    raw = params.get(key)
    if not isinstance(raw, str) or not raw.strip():
        return None
    title = page_title(raw)
    if title is None:
        logger.warning("Ignoring unusable %s reference: %r", key, raw)
        return None
    return page_url(title)
