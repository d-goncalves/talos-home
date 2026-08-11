"""Comparing Pi-hole v6 config values, so tasks are honestly idempotent.

`pihole-FTL --config <key>` prints a value back in its own format, not JSON:

    dns.upstreams   ->  [ 127.0.0.1#5335, 1.0.0.1, 1.1.1.1 ]
    dns.hosts       ->  []
    dhcp.active     ->  false
    dns.piholePTR   ->  PI.HOLE

Writing takes JSON. So a task that only compares the two representations
directly would report `changed` on every run forever, which makes the whole
playbook useless as a drift check. This normalises both sides instead.
"""

from __future__ import annotations


def _normalise(value):
    """A comparable form for either side of the comparison."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value]
    if isinstance(value, str):
        text = value.strip()
        # An array as FTL prints it: "[ a, b ]" or "[]".
        if text.startswith("[") and text.endswith("]"):
            inner = text[1:-1].strip()
            return [part.strip() for part in inner.split(",")] if inner else []
        return text
    return str(value)


def pihole_matches(current: str, desired) -> bool:
    """True when FTL already holds `desired`, so nothing needs writing."""
    return _normalise(current) == _normalise(desired)


class FilterModule:
    def filters(self):
        return {"pihole_matches": pihole_matches}
