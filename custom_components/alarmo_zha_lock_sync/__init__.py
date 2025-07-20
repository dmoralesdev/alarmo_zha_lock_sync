"""Alarmo ↔ ZHA lock user code synchronization."""
from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from typing import Dict, Any, Optional

from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import storage
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, STORAGE_VERSION, STORAGE_KEY

_LOGGER = logging.getLogger(__name__)

# --------------------------------------------------------------------
# Helpers for dynamic import without blocking the event loop
# --------------------------------------------------------------------
async def _async_import_module(hass: HomeAssistant, module_name: str):
    """Import a module in an executor thread to avoid blocking."""
    return await hass.async_add_executor_job(importlib.import_module, module_name)

async def _load_usermanager(hass: HomeAssistant) -> Optional[object]:
    """Try to locate Alarmo's UserManager class dynamically."""
    candidate_paths = [
        "custom_components.alarmo.core.users",
        "custom_components.alarmo.users",
        "custom_components.alarmo.const",
    ]
    for path in candidate_paths:
        try:
            mod = await _async_import_module(hass, path)
            if hasattr(mod, "UserManager"):
                return mod
        except ModuleNotFoundError:
            continue
        except Exception as err:  # pragma: no cover
            _LOGGER.debug("Import error for %s: %s", path, err)
    # Fallback: search loaded modules if Alarmo already imported elsewhere
    for module_name, module in sys.modules.items():
        if module_name.startswith("custom_components.alarmo") and hasattr(module, "UserManager"):
            return module
    return None

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Setup via YAML (not supported, retain for compatibility)."""
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
        except Exception as err:
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

    # ------------------------------------------------------------------
    # Monkey‑patch Alarmo user creation dynamically
    # ------------------------------------------------------------------
    users_mod = await _load_usermanager(hass)
    if users_mod is None:
        _LOGGER.error(
            "Alarmo UserManager class not found. "
            "User‑creation sync will not work; enable/disable sync still active."
        )
    else:
        create_attrs = ["async_create_user", "async_add_user", "async_add"]
        orig_func = None
        patched_attr = None
        for attr in create_attrs:
            if hasattr(users_mod.UserManager, attr):
                orig_func = getattr(users_mod.UserManager, attr)
                patched_attr = attr
                break

        if orig_func:

            async def patched(self, user: Dict[str, Any], *args, **kwargs):  # type: ignore[no-self-use]
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
        else:
            _LOGGER.error(
                "User creation method not found in Alarmo.UserManager. "
                "User‑creation sync will be disabled."
            )

    # ------------------------------------------------------------------
    # Listen for enable/disable service calls
    # ------------------------------------------------------------------
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
