"""Alarmo ↔ ZHA lock user code synchronization."""
from __future__ import annotations

import asyncio
import importlib
import logging
from typing import Dict, Any, Optional

from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import storage
from homeassistant.setup import async_when_setup
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, STORAGE_VERSION, STORAGE_KEY

_LOGGER = logging.getLogger(__name__)

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
        for slot in range(1, 255):
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
            _LOGGER.info("Synced %s to slot %s", name, slot)
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
                            f"Payload: [entity_id: {lock_entity}, code_slot: {slot}, user_code: {code}]. "
                            f"Error: {err}"
                        ),
                    },
                )
            )

    async def _clear_code(name: str, slot: int) -> None:
        try:
            await hass.services.async_call(
                "zha",
                "clear_lock_user_code",
                {"entity_id": lock_entity, "code_slot": slot},
                blocking=True,
            )
            _LOGGER.info("Cleared slot %s for %s", slot, name)
        except Exception as err:
            _LOGGER.warning("Failed clearing %s: %s", name, err)

    # ---------------------------------------------------------------
    # Patch AlarmoStorage.async_create_user
    # ---------------------------------------------------------------
    async def _patch_storage(hass: HomeAssistant, _component) -> None:
        store_mod = await hass.async_add_executor_job(
            importlib.import_module,
            "custom_components.alarmo.store",
        )
        StorageCls = getattr(store_mod, "AlarmoStorage", None)
        if StorageCls is None:
            _LOGGER.error("AlarmoStorage class not found; creation sync disabled")
            return

        if getattr(StorageCls, "_zha_sync_patched", False):
            return

        original_fn = StorageCls.async_create_user

        def patched(self, data: dict) -> Any:  # synchronous method
            plain_code = data.get("code")
            plain_name = data.get("name", "")
            result = original_fn(self, data)
            if plain_code:
                slot = mapping.get(plain_name) or _next_free_slot()
                mapping[plain_name] = slot
                _persist()
                hass.async_create_task(_push_code(plain_name, plain_code, slot))
            return result

        StorageCls.async_create_user = patched
        StorageCls._zha_sync_patched = True
        _LOGGER.info("Patched AlarmoStorage.async_create_user")

    async_when_setup(hass, "alarmo", _patch_storage)

    # ---------------------------------------------------------------
    # Listen for enable/disable user services
    # ---------------------------------------------------------------
    @callback
    async def _handle_alarmo_service(event):
        if event.data.get("domain") != "alarmo":
            return
        svc = event.data.get("service")
        data = event.data.get("service_data", {})
        name = data.get("name")
        if not name:
            return
        if svc == "enable_user" and (code := data.get("code")):
            slot = mapping.get(name) or _next_free_slot()
            mapping[name] = slot
            _persist()
            await _push_code(name, code, slot)
        elif svc == "disable_user":
            if (slot := mapping.get(name)):
                await _clear_code(name, slot)

    remove_listener = hass.bus.async_listen(EVENT_CALL_SERVICE, _handle_alarmo_service)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = remove_listener
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if (remove := hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)):
        remove()
    return True
