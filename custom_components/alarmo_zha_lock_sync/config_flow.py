"""Config flow for Alarmo ZHA Lock Sync."""
from __future__ import annotations
from homeassistant import config_entries
import voluptuous as vol
from .const import DOMAIN

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""
    VERSION = 1
    MINOR_VERSION = 0

    async def async_step_user(self, user_input=None):
        """Let user pick lock."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title=f"Lock: {user_input['lock_entity']}", data=user_input)
        lock_entities = [state.entity_id for state in self.hass.states.async_all("lock")]
        if not lock_entities:
            return self.async_abort(reason="no_locks_found")
        schema = vol.Schema({vol.Required("lock_entity"): vol.In(lock_entities)})
        return self.async_show_form(step_id="user", data_schema=schema)
