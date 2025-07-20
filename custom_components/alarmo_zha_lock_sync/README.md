# Alarmo ZHA Lock Sync

This integration synchronizes Alarmo alarm users with a Zigbee door lock (ZHA).

## Features
* UI setup: pick your lock from a dropdown.
* Automatically writes user codes when you create or enable users in Alarmo.
* Clears codes when users are disabled.
* Persists a user→slot mapping across restarts.
* Notifies you if the lock is unreachable.
* Supports log filtering to hide PINs.

## Installation
1. Add this repository to HACS as a custom integration.
2. Install and restart Home Assistant.
3. Go to **Settings → Devices & Services → + Add Integration**, search for *Alarmo ZHA Lock Sync*, and select your lock.
4. (Optional) Add a logger filter:

   ```yaml
   logger:
     default: info
     filters:
       custom_components.alarmo_zha_lock_sync:
         - '"user_code":'
   ```
