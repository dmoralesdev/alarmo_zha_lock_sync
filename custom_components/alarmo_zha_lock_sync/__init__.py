"""Alarmo ↔ ZHA lock user code synchronization."""
from __future__ import annotations

import importlib
import logging
import sys
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
        raise ValueError("No free lock slots")

    async def _push_code(name: str, code: str, slot: int) -> None:
        try:
            await hass.services.async_call(
                "zha",
                "set_lock_user_code",
                {"entity_id": lock_entity, "code_slot": slot, "user_code": code},
                blocking=True,
            )
            _LOGGER.debug("Pushed code for %s slot %s", name, slot)
        except Exception as err:
            _LOGGER.warning("Push failed for %s: %s", name, err)
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
            _LOGGER.debug("Cleared slot %s for %s", slot, name)
        except Exception as err:
            _LOGGER.warning("Clear failed for %s: %s", name, err)
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

    # ---------------------------------------------------------------
    # Alarmo patching helpers
    # ---------------------------------------------------------------
    def _find_usermanager_in_sys() -> Optional[object]:
        for mod_name, mod in sys.modules.items():
            if mod_name.startswith("custom_components.alarmo") and hasattr(mod, "UserManager"):
                return mod
        return None

    def _import_usermanager() -> Optional[object]:
        try:
            module = importlib.import_module("custom_components.alarmo.core.users")
            if hasattr(module, "UserManager"):
                return module
        except ModuleNotFoundError:
            pass
        return None

    def _coordinator_usermanager() -> Optional[object]:
        alarmo_data = hass.data.get("alarmo")
        if not alarmo_data:
            return None
        # Alarmo stores coordinators by area (alarm entity). Iterate to find one exposing user_manager.
        if isinstance(alarmo_data, dict):
            for coord in alarmo_data.values():
                if hasattr(coord, "user_manager"):
                    return coord
        return None

    def _locate_usermanager() -> Optional[object]:
        return _find_usermanager_in_sys() or _import_usermanager() or _coordinator_usermanager()

    async def _patch_usermanager(hass: HomeAssistant, _component) -> None:
        users_mod_or_coord = _locate_usermanager()
        if users_mod_or_coord is None:
            _LOGGER.error("Alarmo UserManager still not found; creation sync disabled")
            return

        # Determine the actual object holding methods
        if hasattr(users_mod_or_coord, "UserManager"):
            target_cls = users_mod_or_coord.UserManager
        else:
            # coordinator instance with user_manager attribute that has create_user method
            target_cls = users_mod_or_coord.user_manager.__class__  # type: ignore[attr-defined]

        creation_attr = next(
            (a for a in ("async_create_user", "async_add_user", "async_add") if hasattr(target_cls, a)),
            None,
        )
        if creation_attr is None:
            _LOGGER.error("No user‑creation method found on UserManager class")
            return

        original_fn = getattr(target_cls, creation_attr)

        async def patched(self, user: Dict[str, Any], *args, **kwargs):  # type: ignore[no-self-use]
            await original_fn(self, user, *args, **kwargs)  # type: ignore[misc]
            name = user.get("name")
            code = user.get("code")
            if not (name and code):
                return
            slot = mapping.get(name) or _next_free_slot()
            mapping[name] = slot
            _persist()
            await _push_code(name, code, slot)

        setattr(target_cls, creation_attr, patched)  # type: ignore[arg-type]
        _LOGGER.info("Patched Alarmo %s.%s", target_cls.__name__, creation_attr)

    # Schedule the patch
    async_when_setup(hass, "alarmo", _patch_usermanager)

    # ---------------------------------------------------------------
    # Listen for enable/disable user services
    # ---------------------------------------------------------------
    @callback
    async def _handle_event(event) -> None:
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
            if slot := mapping.get(name):
                await _clear_code(name, slot)

    remove = hass.bus.async_listen(EVENT_CALL_SERVICE, _handle_event)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = remove
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if remove := hass.data.get(DOMAIN, {}).pop(entry.entry_id, None):
        remove()
    return True
