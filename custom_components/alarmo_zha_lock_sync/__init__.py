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
# Public HA entry points
# ----------------------------------------------------------------------
async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # noqa: D401
    """This integration has no YAML setup."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """GUI-based setup: user has chosen a Zigbee lock entity."""
    lock_entity: str = entry.data["lock_entity"]

    # Persistent mapping: Alarmo user → lock slot
    store = storage.Store(hass, STORAGE_VERSION, STORAGE_KEY)
    mapping: Dict[str, int] = await store.async_load() or {}

    def _persist() -> None:
        hass.async_create_task(store.async_save(mapping))

    # ------------------------------------------------------------------
    # Lock helpers
    # ------------------------------------------------------------------
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
                {"entity_id": lock_entity, "code_slot": slot, "user_code": code},
                blocking=True,
            )
            _LOGGER.info("Synced %s to slot %s", name, slot)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed to sync %s: %s", name, err)
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
            _LOGGER.info("Cleared slot %s for %s", slot, name)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed to clear slot %s: %s", slot, err)
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
    # Alarmo patching
    # ------------------------------------------------------------------
    async def _find_usermanager() -> Optional[object]:
        """Return module that exposes UserManager, or None."""
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

    async def _patch_usermanager(hass: HomeAssistant, _component) -> None:
        """Patch Alarmo.UserManager once Alarmo is ready."""
        users_mod = await _find_usermanager()
        if users_mod is None:
            _LOGGER.error("Alarmo UserManager not found; creation sync disabled")
            return

        # Find creation method
        creation_attr = next(
            (attr for attr in ("async_create_user", "async_add_user", "async_add") if hasattr(users_mod.UserManager, attr)),
            None,
        )
        if creation_attr is None:
            _LOGGER.error("No user‑creation method on Alarmo.UserManager")
            return

        original_fn = getattr(users_mod.UserManager, creation_attr)

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

        setattr(users_mod.UserManager, creation_attr, patched)  # type: ignore[arg-type]
        _LOGGER.info("Patched Alarmo.%s successfully", creation_attr)

    # Schedule patch after Alarmo finishes setup
    async_when_setup(hass, "alarmo", _patch_usermanager)

    # ------------------------------------------------------------------
    # Listen for enable/disable user services
    # ------------------------------------------------------------------
    @callback
    async def _handle_alarmo_service(event) -> None:
        if event.data.get("domain") != "alarmo":
            return
        service = event.data.get("service")
        data = event.data.get("service_data", {})
        name = data.get("name")
        if not name:
            return

        if service == "enable_user":
            code = data.get("code")
            if not code:
                return
            slot = mapping.get(name) or _next_free_slot()
            mapping[name] = slot
            _persist()
            await _push_code(name, code, slot)

        elif service == "disable_user":
            slot = mapping.get(name)
            if slot:
                await _clear_code(name, slot)

    remove_listener = hass.bus.async_listen(EVENT_CALL_SERVICE, _handle_alarmo_service)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = remove_listener

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration."""
    remove_listener = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if remove_listener:
        remove_listener()
    return True
