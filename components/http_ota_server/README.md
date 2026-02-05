# HTTP OTA Server Component

Push-based OTA server for ESP-IDF. Accepts firmware uploads via HTTP POST.

## Usage

### 1. Copy component to your project

```bash
cp -r components/http_ota_server /path/to/your/project/components/
```

### 2. Add to your main code

```c
#include "http_ota_server.h"

// After WiFi is connected:
void app_main(void)
{
    // ... WiFi setup ...

    // Start OTA server with defaults (port 8080)
    http_ota_server_config_t config = HTTP_OTA_SERVER_DEFAULT_CONFIG();
    config.firmware_version = "1.0.0";
    http_ota_server_start(&config);

    // Your application code...
}
```

### 3. Push firmware from host

```bash
# Simple push
curl -X POST http://192.168.0.123:8080/ota --data-binary @build/my_app.bin

# Or use the helper script
./scripts/ota-push.sh 192.168.0.123 build/my_app.bin
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check, returns `{"status":"ok"}` |
| `/version` | GET | Returns `{"version":"1.0.0","idf_version":"5.x"}` |
| `/ota` | POST | Upload firmware binary |

## Configuration

```c
http_ota_server_config_t config = {
    .port = 8080,                    // HTTP server port
    .firmware_version = "1.0.0",     // Reported version
    .on_ota_start = my_start_cb,     // Called when OTA begins
    .on_ota_complete = my_done_cb,   // Called before reboot
    .on_ota_fail = my_fail_cb,       // Called on failure
};
```

## Partition Table

Ensure your `partitions.csv` has OTA partitions:

```csv
# Name,   Type, SubType, Offset,  Size,    Flags
nvs,      data, nvs,     0x9000,  0x6000,
phy_init, data, phy,     0xf000,  0x1000,
otadata,  data, ota,     0x10000, 0x2000,
ota_0,    app,  ota_0,   0x20000, 0x1E0000,
ota_1,    app,  ota_1,   0x200000,0x1E0000,
```

Or use a predefined partition table in `sdkconfig`:
```
CONFIG_PARTITION_TABLE_TWO_OTA=y
```

## Error Handling

The server validates:
- Non-empty firmware
- ESP32 magic bytes (0xE9)
- Firmware fits in partition
- Flash write success
- Image validation

On error, HTTP 4xx/5xx is returned with JSON error message.

## Security Notes

- No authentication by default - add if needed for production
- Runs on HTTP (not HTTPS) - suitable for local network only
- Consider adding version check to prevent downgrades
