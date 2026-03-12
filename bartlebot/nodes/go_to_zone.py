import asyncio

from wizwalker import XYZ

ZONE_CHANGE_TIMEOUT = 30.0
POLL_INTERVAL = 0.25


async def run(machine, clients: list, params: dict) -> str:
    """
    Transition all clients from one zone to another.

    Params:
        from_zone (str): Zone clients must be in when this node is called.
        to_zone (str):   Zone clients must be in when this node completes.
        xyz (dict):      {"x": float, "y": float, "z": float} — a point inside
                         the destination zone's transition bounding box.
                         Teleporting clients here triggers the zone change automatically.
    """
    from_zone: str = params["from_zone"]
    to_zone: str = params["to_zone"]
    xyz_data: dict = params["xyz"]
    target = XYZ(xyz_data["x"], xyz_data["y"], xyz_data["z"])

    # 1. Verify all clients are in from_zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    if not all(z == from_zone for z in zones):
        return "FAILURE"

    # 2. Teleport all clients into the transition bounding box.
    await asyncio.gather(*[c.teleport(target) for c in clients])

    # 3. Wait for all clients to leave from_zone (zone change triggered by bounding box).
    if not await _wait_until(lambda: _all_left_zone(clients, from_zone), ZONE_CHANGE_TIMEOUT):
        return "FAILURE"

    # 4. Wait for loading screens to finish.
    await _wait_until(lambda: _none_loading(clients), timeout=None)

    # 5. Verify all clients arrived in to_zone.
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    if not all(z == to_zone for z in zones):
        return "FAILURE"

    return "SUCCESS"


async def _wait_until(condition_coro, timeout: float | None) -> bool:
    """Poll condition_coro every POLL_INTERVAL until it returns True or timeout expires."""
    async def _poll():
        while not await condition_coro():
            await asyncio.sleep(POLL_INTERVAL)

    try:
        await asyncio.wait_for(_poll(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


async def _all_left_zone(clients: list, zone: str) -> bool:
    zones = await asyncio.gather(*[c.zone_name() for c in clients])
    return all(z != zone for z in zones)


async def _none_loading(clients: list) -> bool:
    loading = await asyncio.gather(*[c.is_loading() for c in clients])
    return not any(loading)
