"""Alarmo ↔ ZHA lock user code synchronization."""
from __future__ import annotations

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

# ----------------------------------------------------------------------
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
        raise ValueError("No free slots available on lock")

    async def _push_code(name: str, code: str, slot: int) -> None:
        try:
            await hass.services.async_call(
                "zha",
                "set_lock_user_code",
                {"entity_id": lock_entity, "code_slot": slot, "user_code": code},
                blocking=True,
            )
            _LOGGER.debug("Synced code for %s to slot %s", name, slot)
        except Exception as err:
            _LOGGER.warning("Failed syncing %s: %s", name, err)
            hass.async_create_task(
                hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "title": "Alarmo ↔ ZHA Lock Sync",
                        "message": f"Could not write code for {name} to {lock_entity}: {err}",
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
            _LOGGER.debug("Cleared slot %s for %s", slot, name)
        except Exception as err:
            _LOGGER.warning("Failed clearing slot %s: %s", slot, err)
            hass.async_create_task(
                hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "title": "Alarmo ↔ ZHA Lock Sync",
                        "message": f"Could not clear code for {name} on {lock_entity}: {err}",
                    },
                )
            )

    # ---------------------------------------------------------------
    # Patch Alarmo store.async_create_user
    # ---------------------------------------------------------------
    async def _patch_store(hass: HomeAssistant, _component) -> None:
        try:
            store_mod = importlib.import_module("custom_components.alarmo.store")
        except ModuleNotFoundError as e:
            _LOGGER.error("Alarmo store module not found: %s", e)
            return

        if not hasattr(store_mod, "AlarmoStorage"):
            _LOGGER.error("AlarmoStorage class not found in Alarmo store")
            return

        StoreCls = store_mod.AlarmoStorage

        if hasattr(StoreCls, "__alarmo_zls_patched__"):
            # Already patched
            return

        original = StoreCls.async_create_user

        def patched(self, data: dict):  # pylint: disable=missing-docstring
            result = original(self, data)
            name = result.name
            code = data.get("code")  # plain code still present
            if name and code:
                slot = mapping.get(name) or _next_free_slot()
                mapping[name] = slot
                _persist()
                hass.async_create_task(_push_code(name, code, slot))
            return result

        StoreCls.async_create_user = patched  # type: ignore[assignment]
        setattr(StoreCls, "__alarmo_zls_patched__", True)
        _LOGGER.info("Patched AlarmoStorage.async_create_user for code sync")

    async_when_setup(hass, "alarmo", _patch_store)

    # ---------------------------------------------------------------
    # Handle enable/disable service calls to keep mapping in sync
    # ---------------------------------------------------------------
    @callback
    async def _handle_service(event) -> None:
        if event.data.get("domain") != "alarmo":
            return
        service = event.data.get("service")
        data = event.data.get("service_data", {})
        name = data.get("name")
        if not name:
            return
        if service == "enable_user" and (code := data.get("code")):
            slot = mapping.get(name) or _next_free_slot()
            mapping[name] = slot
            _persist()
            await _push_code(name, code, slot)
        elif service == "disable_user":
            if (slot := mapping.get(name)):
                await _clear_code(name, slot)

    remove = hass.bus.async_listen(EVENT_CALL_SERVICE, _handle_service)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = remove
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if (remove := hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)):
        remove()
    return True
