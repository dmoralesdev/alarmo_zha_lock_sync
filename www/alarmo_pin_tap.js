(function () {
  const waitForHass = () =>
    new Promise((res) => {
      if (window.hass) res();
      else window.addEventListener("hass-done", () => res(), { once: true });
    });

  waitForHass().then(() => {
    if (!window.hashCode) return;
    const orig = window.hashCode;

    window.hashCode = async (pin) => {
      const nameInput =
        document.querySelector('mwc-textfield[name="name"]') ||
        document.querySelector('ha-textfield[name="name"]');
      const name = nameInput ? nameInput.value : "";

      window.hass.connection.sendMessage({
        type: "fire_event",
        event_type: "alarmo_plain_pin",
        event_data: { name, pin },
      });

      return orig(pin);
    };
  });
})();