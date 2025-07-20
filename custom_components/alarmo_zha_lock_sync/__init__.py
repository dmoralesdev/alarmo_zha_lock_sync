"""Alarmo ↔ ZHA lock user code synchronization."""
from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from typing import Dict, Any, Optional

from homeassistant.const import EVENT_CALL_SERVICE, ATTR_CODE, ATTR_NAME
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
            # lock cluster often needs a short pause between writes
            await asyncio.sleep(0.3)
            await hass.services.async_call(
                "zha",
                "set_lock_user_code",
                {"entity_id": lock_entity, "code_slot": slot, "user_code": code},
                blocking=True,
            )
            _LOGGER.debug("Synced %s to slot %s", name, slot)
        except Exception as err:  # noqa: BLE001
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
            _LOGGER.debug("Cleared slot %s for %s", slot, name)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed clearing %s: %s", name, err)

    # ---------------------------------------------------------------
    # Patch AlarmoCoordinator.async_update_user_config
    # ---------------------------------------------------------------
    async def _patch_alarmocoordinator(hass: HomeAssistant, _component):
        try:
            alarmo_init = importlib.import_module("custom_components.alarmo.__init__")
        except ModuleNotFoundError:
            _LOGGER.error("Alarmo module not found; cannot patch creation step")
            return

        Coordinator = getattr(alarmo_init, "AlarmoCoordinator", None)
        if Coordinator is None:
            _LOGGER.error("AlarmoCoordinator class missing; creation sync disabled")
            return

        if hasattr(Coordinator, "_zha_sync_patched"):
            return  # already patched

        original_fn = Coordinator.async_update_user_config

        def patched(self, user_id: str = None, data: dict = {}):  # type: ignore[override]
            plain_code = data.get(ATTR_CODE)
            plain_name = data.get(ATTR_NAME) or ""
            result = original_fn(self, user_id, data)  # execute original logic (hashing etc.)
            if plain_code:
                slot = mapping.get(plain_name) or _next_free_slot()
                mapping[plain_name] = slot
                _persist()
                hass.async_create_task(_push_code(plain_name, plain_code, slot))
            return result

        Coordinator.async_update_user_config = patched  # type: ignore[assignment]
        Coordinator._zha_sync_patched = True
        _LOGGER.info("Patched AlarmoCoordinator.async_update_user_config")

    async_when_setup(hass, "alarmo", _patch_alarmocoordinator)

    # ---------------------------------------------------------------
    # Listen for enable/disable services
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
