// Injected script to grab plaintext PIN from Alarmo user dialog
(function() {
  const waitForHass = () =>
    new Promise(resolve => {
      if (window.hassConnection && window.hassConnection.then) {
        window.hassConnection.then(({conn}) => resolve(conn));
      } else if (window.hass) {
        resolve(window.hass);
      } else {
        setTimeout(() => resolve(waitForHass()), 500);
      }
    });

  const inject = () => {
    if (window.__alarmo_pin_hook_installed) return;
    window.__alarmo_pin_hook_installed = true;

    const origHashCode = window.hashCode;
    window.hashCode = async function(pin) {
      try {
        const el = document.querySelector("alarmo-user-editor");
        const nameInput = el && el.shadowRoot.querySelector("mwc-textfield");
        const name = nameInput ? nameInput.value : "";
        const conn = await waitForHass();
        conn.sendMessage({
          type: "fire_event",
          event_type: "alarmo_plain_pin",
          event_data: { name: name || "Unknown", pin }
        });
      } catch (e) {
        console.error("PIN hook error", e);
      }
      return origHashCode(pin);
    };
    console.log("Alarmo PIN hook installed");
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", inject);
  } else {
    inject();
  }
})();
