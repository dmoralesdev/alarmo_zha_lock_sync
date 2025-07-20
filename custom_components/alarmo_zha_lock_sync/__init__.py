
from __future__ import annotations
import asyncio, logging
from typing import Dict
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.helpers import storage
from homeassistant.config_entries import ConfigEntry
from .const import DOMAIN, STORAGE_VERSION, STORAGE_KEY

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    lock_entity = entry.data["lock_entity"]
    store = storage.Store(hass, STORAGE_VERSION, STORAGE_KEY)
    mapping: Dict[str, int] = await store.async_load() or {}

    def _persist(): hass.async_create_task(store.async_save(mapping))

    def _next_free_slot():
        used = set(mapping.values())
        for slot in range(1, 255):
            if slot not in used:
                return slot
        raise ValueError("No free lock slots")

    async def _push_code(name: str, code: str, slot: int):
        try:
            await asyncio.sleep(0.3)
            await hass.services.async_call("zha", "set_lock_user_code", {
                "entity_id": lock_entity,
                "code_slot": slot,
                "user_code": code
            }, blocking=True)
            _LOGGER.info("Synced %s to slot %s", name, slot)
        except Exception as err:
            _LOGGER.warning("Failed syncing %s: %s", name, err)
            await hass.services.async_call("persistent_notification", "create", {
                "title": "Alarmo ↔ ZHA Lock Sync",
                "message": f"Could not write code for {name} to {lock_entity}: Payload: [entity_id: {lock_entity}, code_slot: {slot}, user_code: {code}]. Error: {err}",
            })

    async def _clear_code(name: str, slot: int):
        try:
            await hass.services.async_call("zha", "clear_lock_user_code", {
                "entity_id": lock_entity,
                "code_slot": slot
            }, blocking=True)
        except Exception as err:
            _LOGGER.warning("Failed clearing code for %s: %s", name, err)

    @callback
    async def _handle_alarmo_service(event):
        if event.data.get("domain") != "alarmo": return
        svc = event.data.get("service")
        data = event.data.get("service_data", {})
        name = data.get("name")
        if not name: return
        if svc == "enable_user" and (code := data.get("code")):
            slot = mapping.get(name) or _next_free_slot()
            mapping[name] = slot
            _persist()
            await _push_code(name, code, slot)
        elif svc == "disable_user":
            if (slot := mapping.get(name)):
                await _clear_code(name, slot)

    @callback
    def _handle_plain_pin(event):
        name = event.data.get("name", "unnamed")
        pin = event.data["pin"]
        slot = mapping.get(name) or _next_free_slot()
        mapping[name] = slot
        _persist()
        hass.async_create_task(_push_code(name, pin, slot))

    hass.bus.async_listen("alarmo_plain_pin", _handle_plain_pin)
    remove = hass.bus.async_listen(EVENT_CALL_SERVICE, _handle_alarmo_service)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = remove
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if (remove := hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)):
        remove()
    return True
