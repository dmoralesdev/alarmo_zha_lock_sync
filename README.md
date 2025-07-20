# Alarmo ZHA Lock Sync

Home Assistant custom integration that keeps a Zigbee door lock's user codes
synchronized with Alarmo alarm users.

- UI config flow: pick the lock from a dropdown.
- Creates a persistent slot mapping per user.
- Removes codes when users are disabled.
- Sends persistent notifications when the lock is unreachable.
- Optional logger filter to mask PINs.

See `custom_components/alarmo_zha_lock_sync/README.md` for full details.
