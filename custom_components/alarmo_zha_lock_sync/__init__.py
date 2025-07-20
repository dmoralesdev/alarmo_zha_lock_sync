"""Alarmo ↔ ZHA lock user code sync using front-end PIN capture."""
from __future__ import annotations
import asyncio
import logging
from typing import Dict

from homeassistant.core import HomeAssistant, callback
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.helpers import storage
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, STORAGE_VERSION, STORAGE_KEY

_LOGGER = logging.getLogger(__name__)
MAX_SLOTS = 50  # adjust if your lock supports fewer

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    lock_entity: str = entry.data["lock_entity"]
    store = storage.Store(hass, STORAGE_VERSION, STORAGE_KEY)
    mapping: Dict[str, int] = await store.async_load() or {}

    def _persist() -> None:
        hass.async_create_task(store.async_save(mapping))

    def _next_free_slot() -> int:
        used = set(mapping.values())
        for slot in range(1, MAX_SLOTS + 1):
            if slot not in used:
                return slot
        raise ValueError("No free lock slots")

    async def _push_code(name: str, code: str, slot: int) -> None:
        try:
            await asyncio.sleep(0.3)
            await hass.services.async_call(
                "zha",
                "set_lock_user_code",
                {"entity_id": lock_entity, "code_slot": slot, "user_code": code},
                blocking=True,
            )
            _LOGGER.info("Synced PIN for %s to slot %s", name, slot)
        except Exception as err:
            _LOGGER.warning("Failed syncing %s: %s", name, err)
            hass.async_create_task(
                hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "title": "Alarmo ↔ ZHA Lock Sync",
                        "message": (
                            f"Could not write code for {name} to {lock_entity}: "
                            f"[slot {slot}] Error: {err}"
                        ),
                    },
                )
            )

    async def _clear_code(name: str, slot: int) -> None:
        try:
            await hass.services.async_call(
                "zha", "clear_lock_user_code",
                {"entity_id": lock_entity, "code_slot": slot},
                blocking=True,
            )
            _LOGGER.info("Cleared slot %s for %s", slot, name)
        except Exception as err:
            _LOGGER.warning("Failed clearing code for %s: %s", name, err)

    # listen for custom event sent by front‑end
    @callback
    async def _handle_plain_pin(event):
        name = event.data.get("name")
        pin = event.data.get("pin")
        if not (name and pin):
            return
        slot = mapping.get(name) or _next_free_slot()
        mapping[name] = slot
        _persist()
        await _push_code(name, pin, slot)
    hass.bus.async_listen("alarmo_plain_pin", _handle_plain_pin)

    # Still handle enable/disable if code shown
    @callback
    async def _handle_service(event):
        if event.data.get("domain") != "alarmo":
            return
        svc = event.data.get("service")
        data = event.data.get("service_data", {})
        name = data.get("name")
        if not name:
            return
        if svc == "disable_user":
            if (slot := mapping.get(name)):
                await _clear_code(name, slot)
    remove_listener = hass.bus.async_listen(EVENT_CALL_SERVICE, _handle_service)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = remove_listener
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if (remove := hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)):
        remove()
    return True
