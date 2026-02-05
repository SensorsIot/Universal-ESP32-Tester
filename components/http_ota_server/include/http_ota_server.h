/**
 * @file http_ota_server.h
 * @brief HTTP OTA Server Component for ESP-IDF
 *
 * Push-based OTA server that accepts firmware uploads via HTTP POST.
 * Compatible with any HTTP client (curl, Python requests, etc.)
 *
 * Usage:
 *   1. Call http_ota_server_start() after WiFi is connected
 *   2. POST firmware to http://<esp32-ip>:8080/ota
 *   3. ESP32 validates, writes, and reboots automatically
 *
 * From host:
 *   curl -X POST http://192.168.0.123:8080/ota --data-binary @firmware.bin
 */

#pragma once

#include "esp_err.h"
#include "esp_http_server.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief OTA server configuration
 */
typedef struct {
    uint16_t port;              /**< HTTP server port (default: 8080) */
    const char *firmware_version; /**< Current firmware version string */
    void (*on_ota_start)(void); /**< Callback before OTA starts (optional) */
    void (*on_ota_complete)(void); /**< Callback after OTA completes, before reboot (optional) */
    void (*on_ota_fail)(const char *error); /**< Callback on OTA failure (optional) */
} http_ota_server_config_t;

/**
 * @brief Default configuration macro
 */
#define HTTP_OTA_SERVER_DEFAULT_CONFIG() { \
    .port = 8080, \
    .firmware_version = "0.0.0", \
    .on_ota_start = NULL, \
    .on_ota_complete = NULL, \
    .on_ota_fail = NULL, \
}

/**
 * @brief Start the HTTP OTA server
 *
 * @param config Server configuration (NULL for defaults)
 * @return ESP_OK on success, error code otherwise
 */
esp_err_t http_ota_server_start(const http_ota_server_config_t *config);

/**
 * @brief Stop the HTTP OTA server
 *
 * @return ESP_OK on success
 */
esp_err_t http_ota_server_stop(void);

/**
 * @brief Check if OTA server is running
 *
 * @return true if running
 */
bool http_ota_server_is_running(void);

/**
 * @brief Get current firmware version
 *
 * @return Version string
 */
const char *http_ota_server_get_version(void);

#ifdef __cplusplus
}
#endif
