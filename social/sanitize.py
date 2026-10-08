import nh3

ALLOWED_TAGS = {
    "p",
    "br",
    "a",
    "span",
    "strong",
    "em",
    "b",
    "i",
    "u",
    "ul",
    "ol",
    "li",
    "del",
    "blockquote",
    "code",
    "pre",
}
ALLOWED_ATTRIBUTES = {
    "a": {"href", "class"},
    "span": {"class"},
}


def sanitize_html(html: str) -> str:
    return nh3.clean(
        html or "",
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer",
    )
