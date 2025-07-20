# Alarmo ZHA Lock Sync

Synchronize Alarmo users with a Zigbee lock via ZHA.
## v0.4.2
* Fixed callback signature for async_when_setup; ensures Alarmo patch runs without TypeError.

## v0.5.0
* Fixed async_when_setup callback signature by defining `_patch_usermanager(hass, _component)`.
* Improved logging.
