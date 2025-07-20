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

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """YAML setup (noop)."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from UI config entry."""
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
            await hass.services.async_call("zha", "set_lock_user_code",
                {"entity_id": lock_entity, "code_slot": slot, "user_code": code},
                blocking=True)
            _LOGGER.debug("Pushed code for %s slot %s", name, slot)
        except Exception as err:
            _LOGGER.warning("Push failed for %s: %s", name, err)
            hass.async_create_task(hass.services.async_call(
                "persistent_notification", "create",
                {"title": "Alarmo ZHA Lock Sync",
                 "message": f"Could not write code for {name} to {lock_entity}: {err}"}))

    async def _clear_code(name: str, slot: int) -> None:
        try:
            await hass.services.async_call("zha", "clear_lock_user_code",
                {"entity_id": lock_entity, "code_slot": slot}, blocking=True)
            _LOGGER.debug("Cleared slot %s for %s", slot, name)
        except Exception as err:
            _LOGGER.warning("Clear failed for %s: %s", name, err)
            hass.async_create_task(hass.services.async_call(
                "persistent_notification", "create",
                {"title": "Alarmo ZHA Lock Sync",
                 "message": f"Could not clear code for {name} on {lock_entity}: {err}"}))

    def _find_usermanager() -> Optional[object]:
        for mod_name, mod in sys.modules.items():
            if mod_name.startswith("custom_components.alarmo") and hasattr(mod, "UserManager"):
                return mod
        try:
            module = importlib.import_module("custom_components.alarmo.core.users")
            if hasattr(module, "UserManager"):
                return module
        except ModuleNotFoundError:
            pass
        return None

    async def _patch(_: HomeAssistant) -> None:
        users_mod = _find_usermanager()
        if users_mod is None:
            _LOGGER.error("Alarmo UserManager not found; creation sync disabled")
            return
        for attr in ("async_create_user","async_add_user","async_add"):
            if hasattr(users_mod.UserManager, attr):
                orig = getattr(users_mod.UserManager, attr)
                break
        else:
            _LOGGER.error("No creation method in UserManager")
            return
        async def patched(self, user: Dict[str, Any], *args, **kwargs):
            await orig(self, user, *args, **kwargs)  # type: ignore[misc]
            name = user.get("name"); code = user.get("code")
            if not (name and code): return
            slot = mapping.get(name) or _next_free_slot()
            mapping[name] = slot; _persist()
            await _push_code(name, code, slot)
        setattr(users_mod.UserManager, attr, patched)  # type: ignore[arg-type]
        _LOGGER.info("Patched Alarmo.%s", attr)

    async_when_setup(hass, "alarmo", _patch)

    @callback
    async def handle_service(event):
        if event.data.get("domain") != "alarmo": return
        svc = event.data.get("service")
        data = event.data.get("service_data", {})
        name = data.get("name"); code = data.get("code")
        if not name: return
        if svc == "enable_user" and code:
            slot = mapping.get(name) or _next_free_slot()
            mapping[name] = slot; _persist()
            await _push_code(name, code, slot)
        elif svc == "disable_user":
            slot = mapping.get(name)
            if slot: await _clear_code(name, slot)

    remove_listener = hass.bus.async_listen(EVENT_CALL_SERVICE, handle_service)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = remove_listener
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload."""
    remove = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if remove: remove()
    return True
