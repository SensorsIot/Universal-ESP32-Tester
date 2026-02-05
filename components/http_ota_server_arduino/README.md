# HTTPOTAServer (Arduino/PlatformIO)

Push-based HTTP OTA server for ESP32. Simple curl POST to update firmware.

**Why not ArduinoOTA?** ArduinoOTA uses espota protocol which requires the ESP32 to connect back to the host. This doesn't work from Docker containers behind NAT. HTTP OTA uses a simple POST request - works from anywhere.

## Installation

### PlatformIO

Add to `platformio.ini`:
```ini
lib_deps =
    https://github.com/SensorsIot/USB-Serial-via-Ethernet.git#main:components/http_ota_server_arduino
```

Or copy the `src/` folder to your project's `lib/HTTPOTAServer/`.

## Usage

```cpp
#include <WiFi.h>
#include <HTTPOTAServer.h>

HTTPOTAServer otaServer(8080);  // Port 8080

void setup() {
    Serial.begin(115200);

    // Connect WiFi
    WiFi.begin("ssid", "password");
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
    }
    Serial.println(WiFi.localIP());

    // Start OTA server
    otaServer.begin("1.0.0");
}

void loop() {
    otaServer.handle();  // Must call in loop!

    // Your code here
}
```

## Push Firmware

From any machine that can reach the ESP32:

```bash
# Build first
pio run

# Push firmware
curl -X POST http://192.168.0.123:8080/ota \
    --data-binary @.pio/build/esp32-c3-base/firmware.bin
```

Or use the helper script:
```bash
ota-push.sh 192.168.0.123 .pio/build/esp32-c3-base/firmware.bin
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Returns `{"status":"ok"}` |
| `/version` | GET | Returns `{"version":"1.0.0"}` |
| `/ota` | POST | Upload firmware binary |

## Callbacks

```cpp
otaServer.onStart([]() {
    Serial.println("OTA starting...");
});

otaServer.onComplete([]() {
    Serial.println("OTA complete, rebooting...");
});

otaServer.onError([](const char* error) {
    Serial.printf("OTA error: %s\n", error);
});

otaServer.onProgress([](size_t current, size_t total) {
    Serial.printf("Progress: %u/%u\n", current, total);
});
```

## Comparison

| Feature | ArduinoOTA (espota) | HTTPOTAServer |
|---------|---------------------|---------------|
| Protocol | UDP + TCP callback | HTTP POST |
| Works from container | ❌ No (NAT blocks callback) | ✅ Yes |
| Client tool | espota.py / PlatformIO | curl / any HTTP client |
| Authentication | Password | None (add if needed) |
| Discovery | mDNS | Must know IP |
