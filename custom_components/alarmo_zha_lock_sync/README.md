# Alarmo ZHA Lock Sync

Synchronize Alarmo users with a Zigbee lock via ZHA.
## v0.4.2
* Fixed callback signature for async_when_setup; ensures Alarmo patch runs without TypeError.

## v0.5.0
* Fixed async_when_setup callback signature by defining `_patch_usermanager(hass, _component)`.
* Improved logging.

## v0.6.0
* Enhanced UserManager discovery: tries sys.modules, direct import, and Alarmo coordinator in hass.data.
* Ensures creation sync works with latest Alarmo releases.

## v0.7.1
* Capture plaintext PIN by patching `AlarmoCoordinator.async_update_user_config` before hashing.
* Adds rate‑limit and detailed payload in notifications.
