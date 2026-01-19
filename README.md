# Maison Protegee Home Assistant Integration

Home Assistant custom integration for Orange Maison Protégée security system.

## Features

- Get alarm status from Orange Maison Protégée web interface
- Control alarm (arm/disarm) via API
- Switch entity for alarm control
- Automatic HTML parsing of status page
- Session-based authentication

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to Integrations
3. Click the three dots menu and select "Custom repositories"
4. Add this repository URL
5. Select "Integration" as the category
6. Click "Add"
7. Find "Maison Protegee" in the HACS integrations list
8. Click "Download"
9. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/maison_protegee` folder to your Home Assistant `custom_components` directory
2. Restart Home Assistant
3. Add the integration via Settings > Devices & Services > Add Integration

## Configuration

1. Go to Settings > Devices & Services
2. Click "Add Integration"
3. Search for "Maison Protegee"
4. Enter your Orange Maison Protégée credentials:
   - **Username**: Your Maison Protégée username
   - **Password**: Your Maison Protégée password

## How It Works

The integration connects to the Orange Maison Protégée web interface at `https://maisonprotegee.orange.fr`:

- **Authentication**: Logs in using your credentials and maintains a session
- **Status Retrieval**: Fetches HTML from `/equipements/status/showBloc.do` and parses the alarm status
- **Status Control**: Calls action endpoints to arm/disarm the alarm

### Status Parsing

The integration parses HTML to extract:
- Alarm status text (e.g., "Alarme désactivée", "Alarme activée")
- Icon classes indicating armed/disarmed state
- Creates a switch entity that reflects the current alarm state

### Control Actions

- **Arm**: Calls `/equipements/status/arm.do`
- **Disarm**: Calls `/equipements/status/disarm.do`

## Development

This integration uses:
- `aiohttp` for async HTTP requests
- `beautifulsoup4` for HTML parsing
- Home Assistant's config flow for setup
- Coordinator pattern for data updates (polls every 30 seconds)

### Local Testing

You can test the API locally using the CLI tool:

1. Install dependencies:
   ```bash
   pip install -r requirements-dev.txt
   ```

2. Run the test CLI:
   ```bash
   # Test authentication
   python test_cli.py <username> <password> auth

   # Get status
   python test_cli.py <username> <password> status

   # Arm the alarm
   python test_cli.py <username> <password> arm

   # Disarm the alarm
   python test_cli.py <username> <password> disarm
   ```

The CLI will show detailed debug output including:
- Authentication status
- Cookie information
- Status data retrieved from the API
- Success/failure of operations

## License

MIT License

