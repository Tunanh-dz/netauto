"""Danh sách thiết bị trong mạng và vai trò của từng thiết bị."""

INVENTORY = {
    "r1": "router",
    "r2": "router",
    "sw1": "switch",
    "sw2": "switch",
    "h-sales": "host",
    "h-guest": "host",
    "h-eng": "host",
    "h-mgmt": "host",
}


def inventory_from_intent(intent: dict) -> dict:
    """Danh sách thiết bị và vai trò, suy ra từ intent (nguồn duy nhất)."""
    nodes = {}
    for role, section in (("router", "routers"), ("switch", "switches"), ("host", "hosts")):
        for name in intent[section]:
            nodes[name] = role
    return nodes