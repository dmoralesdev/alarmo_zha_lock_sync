"""Alarmo ↔ ZHA lock user code synchronization."""
from __future__ import annotations

import importlib
import logging
from typing import Dict, Any

from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import storage
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, STORAGE_VERSION, STORAGE_KEY

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up via YAML (not supported)."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a UI config entry."""
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
        raise ValueError("No free lock slots available")

    async def _push_code(name: str, code: str, slot: int) -> None:
        try:
            await hass.services.async_call(
                "zha",
                "set_lock_user_code",
                {
                    "entity_id": lock_entity,
                    "code_slot": slot,
                    "user_code": code,
                },
                blocking=True,
            )
            _LOGGER.info("Synced code for %s to slot %s", name, slot)
        except Exception as err:  # broad exception acceptable for service failures
            _LOGGER.warning("Failed to push code for %s: %s", name, err)
            hass.async_create_task(
                hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "title": "Alarmo ZHA Lock Sync",
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
            _LOGGER.info("Cleared slot %s for user %s", slot, name)
        except Exception as err:
            _LOGGER.warning("Failed to clear code for %s: %s", name, err)
            hass.async_create_task(
                hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "title": "Alarmo ZHA Lock Sync",
                        "message": f"Could not clear code for {name} on {lock_entity}: {err}",
                    },
                )
            )

    # Monkey‑patch Alarmo user creation
    try:
        users_mod = importlib.import_module("custom_components.alarmo.core.users")
        create_attrs = ["async_create_user", "async_add_user", "async_add"]
        orig_func = None
        for attr in create_attrs:
            if hasattr(users_mod.UserManager, attr):
                orig_func = getattr(users_mod.UserManager, attr)
                patched_attr = attr
                break
        if orig_func is None:
            raise AttributeError("Alarmo user creation method not found")

        async def patched(self, user: Dict[str, Any], *args, **kwargs):
            await orig_func(self, user, *args, **kwargs)  # type: ignore[misc]
            name = user.get("name")
            code = user.get("code")
            if not (name and code):
                return
            slot = mapping.get(name) or _next_free_slot()
            mapping[name] = slot
            _persist()
            await _push_code(name, code, slot)

        setattr(users_mod.UserManager, patched_attr, patched)  # type: ignore[arg-type]
        _LOGGER.debug("Patched Alarmo.%s successfully", patched_attr)
    except Exception as err:
        _LOGGER.error("Failed to patch Alarmo: %s", err)

    # Listen for enable/disable service calls
    @callback
    async def handle_service(event) -> None:
        if event.data.get("domain") != "alarmo":
            return
        svc = event.data.get("service")
        data = event.data.get("service_data", {})
        name = data.get("name")
        if not name:
            return
        if svc == "enable_user":
            code = data.get("code")
            if not code:
                return
            slot = mapping.get(name) or _next_free_slot()
            mapping[name] = slot
            _persist()
            await _push_code(name, code, slot)
        elif svc == "disable_user":
            slot = mapping.get(name)
            if slot:
                await _clear_code(name, slot)

    remove_listener = hass.bus.async_listen(EVENT_CALL_SERVICE, handle_service)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = remove_listener
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the config entry."""
    remove_listener = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if remove_listener:
        remove_listener()
    return True
