# Haier AC

Home Assistant custom integration for local TCP control of compatible Haier air conditioners.

This integration talks directly to the air conditioner's LAN module by IP address, port, and MAC address. It does not use the Haier cloud APIs.

## Features

- Climate entity for power, HVAC mode, target temperature, fan mode, and swing mode
- Local polling over TCP
- Config flow setup from the Home Assistant UI
- Reconfigure flow for updating IP address, port, MAC address, name, and timeout

## Installation

### HACS custom repository

1. In HACS, open **Custom repositories**.
2. Add `https://github.com/banzhanglaolin/HaierAC` as an **Integration** repository.
3. Install **Haier AC**.
4. Restart Home Assistant.
5. Go to **Settings > Devices & services > Add integration** and search for **Haier AC**.

### Manual

1. Copy `custom_components/haier_ac` into your Home Assistant config directory:

   ```text
   /config/custom_components/haier_ac
   ```

2. Restart Home Assistant.
3. Go to **Settings > Devices & services > Add integration** and search for **Haier AC**.

## Configuration

The config flow asks for:

- `Name`: Display name for the climate entity
- `IP address`: Local IP address of the Haier AC module
- `Port`: Local TCP port, default `56800`
- `MAC address`: MAC address of the AC module, with or without separators
- `Timeout`: TCP timeout in seconds, default `5`

The integration tests the local TCP heartbeat before creating a config entry. If setup fails, confirm that Home Assistant can reach the AC module on the same LAN and that the IP, port, and MAC address are correct.

## Limitations

This project is based on observed local protocol frames and may not support every Haier AC model or firmware. If your unit uses a different protocol, setup or commands may fail.

## Development

Run the protocol tests with:

```bash
python -m unittest discover -s tests
```

The tests cover MAC validation, packet construction, heartbeat validation, command response parsing, UART state parsing, and invalid packet handling.

## License

MIT
