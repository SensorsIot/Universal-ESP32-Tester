/**
 * @file http_ota_server.c
 * @brief HTTP OTA Server Implementation
 */

#include "http_ota_server.h"

#include <string.h>
#include <sys/param.h>

#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_app_format.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "http_ota";

// Server state
static httpd_handle_t s_server = NULL;
static http_ota_server_config_t s_config;
static char s_version[32] = "0.0.0";

// OTA buffer size (receive chunks of this size)
#define OTA_BUFFER_SIZE 4096

/**
 * @brief GET /version - Return firmware version
 */
static esp_err_t version_handler(httpd_req_t *req)
{
    char response[128];
    snprintf(response, sizeof(response),
             "{\"version\":\"%s\",\"idf_version\":\"%s\"}",
             s_version, esp_get_idf_version());

    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, response);
    return ESP_OK;
}

/**
 * @brief GET /health - Health check
 */
static esp_err_t health_handler(httpd_req_t *req)
{
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, "{\"status\":\"ok\"}");
    return ESP_OK;
}

/**
 * @brief POST /ota - Receive and install firmware
 */
static esp_err_t ota_handler(httpd_req_t *req)
{
    esp_err_t err;
    esp_ota_handle_t ota_handle = 0;
    const esp_partition_t *update_partition = NULL;
    char *buf = NULL;
    int received = 0;
    int remaining = req->content_len;
    bool ota_started = false;

    ESP_LOGI(TAG, "OTA request received, size: %d bytes", req->content_len);

    // Validate content length
    if (req->content_len == 0) {
        ESP_LOGE(TAG, "Empty firmware");
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Empty firmware");
        return ESP_FAIL;
    }

    // Check minimum size (ESP32 firmware header is at least 24 bytes)
    if (req->content_len < 256) {
        ESP_LOGE(TAG, "Firmware too small: %d bytes", req->content_len);
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Firmware too small");
        return ESP_FAIL;
    }

    // Get update partition
    update_partition = esp_ota_get_next_update_partition(NULL);
    if (update_partition == NULL) {
        ESP_LOGE(TAG, "No OTA partition found");
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "No OTA partition");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Writing to partition: %s (offset 0x%lx, size 0x%lx)",
             update_partition->label,
             (unsigned long)update_partition->address,
             (unsigned long)update_partition->size);

    // Check firmware fits in partition
    if (req->content_len > update_partition->size) {
        ESP_LOGE(TAG, "Firmware too large: %d > %lu",
                 req->content_len, (unsigned long)update_partition->size);
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Firmware too large for partition");
        return ESP_FAIL;
    }

    // Allocate receive buffer
    buf = malloc(OTA_BUFFER_SIZE);
    if (buf == NULL) {
        ESP_LOGE(TAG, "Failed to allocate buffer");
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
        return ESP_FAIL;
    }

    // Callback: OTA starting
    if (s_config.on_ota_start) {
        s_config.on_ota_start();
    }

    // Receive and write firmware
    while (remaining > 0) {
        // Receive chunk
        int recv_len = httpd_req_recv(req, buf, MIN(remaining, OTA_BUFFER_SIZE));

        if (recv_len < 0) {
            if (recv_len == HTTPD_SOCK_ERR_TIMEOUT) {
                ESP_LOGW(TAG, "Receive timeout, retrying...");
                continue;
            }
            ESP_LOGE(TAG, "Receive error: %d", recv_len);
            err = ESP_FAIL;
            goto cleanup;
        }

        if (recv_len == 0) {
            ESP_LOGE(TAG, "Connection closed prematurely");
            err = ESP_FAIL;
            goto cleanup;
        }

        // First chunk: validate firmware header and start OTA
        if (!ota_started) {
            // Check ESP32 firmware magic byte
            esp_app_desc_t *app_desc = NULL;
            esp_image_header_t *header = (esp_image_header_t *)buf;

            if (header->magic != ESP_IMAGE_HEADER_MAGIC) {
                ESP_LOGE(TAG, "Invalid firmware magic: 0x%02x (expected 0x%02x)",
                         header->magic, ESP_IMAGE_HEADER_MAGIC);
                httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid firmware format");
                err = ESP_FAIL;
                goto cleanup;
            }

            // Start OTA
            err = esp_ota_begin(update_partition, OTA_WITH_SEQUENTIAL_WRITES, &ota_handle);
            if (err != ESP_OK) {
                ESP_LOGE(TAG, "esp_ota_begin failed: %s", esp_err_to_name(err));
                httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "OTA begin failed");
                goto cleanup;
            }

            ota_started = true;
            ESP_LOGI(TAG, "OTA started, receiving firmware...");
        }

        // Write chunk to flash
        err = esp_ota_write(ota_handle, buf, recv_len);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "esp_ota_write failed: %s", esp_err_to_name(err));
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Flash write failed");
            goto cleanup;
        }

        received += recv_len;
        remaining -= recv_len;

        // Progress log every 64KB
        if ((received % (64 * 1024)) < OTA_BUFFER_SIZE) {
            ESP_LOGI(TAG, "Progress: %d / %d bytes (%d%%)",
                     received, req->content_len, (received * 100) / req->content_len);
        }
    }

    ESP_LOGI(TAG, "Firmware received: %d bytes", received);

    // Finish OTA
    err = esp_ota_end(ota_handle);
    ota_handle = 0;  // Mark as closed

    if (err != ESP_OK) {
        if (err == ESP_ERR_OTA_VALIDATE_FAILED) {
            ESP_LOGE(TAG, "Firmware validation failed");
            httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Firmware validation failed");
        } else {
            ESP_LOGE(TAG, "esp_ota_end failed: %s", esp_err_to_name(err));
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "OTA finalize failed");
        }
        goto cleanup;
    }

    // Set boot partition
    err = esp_ota_set_boot_partition(update_partition);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_set_boot_partition failed: %s", esp_err_to_name(err));
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Set boot partition failed");
        goto cleanup;
    }

    ESP_LOGI(TAG, "OTA successful! Preparing to reboot...");

    // Send success response
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, "{\"status\":\"ok\",\"message\":\"OTA complete, rebooting...\"}");

    // Callback: OTA complete
    if (s_config.on_ota_complete) {
        s_config.on_ota_complete();
    }

    free(buf);

    // Delay to let response send, then reboot
    vTaskDelay(pdMS_TO_TICKS(500));
    esp_restart();

    return ESP_OK;  // Never reached

cleanup:
    if (ota_handle) {
        esp_ota_abort(ota_handle);
    }
    if (buf) {
        free(buf);
    }
    if (s_config.on_ota_fail) {
        s_config.on_ota_fail("OTA failed");
    }
    return err;
}

// URI handlers
static const httpd_uri_t uri_version = {
    .uri = "/version",
    .method = HTTP_GET,
    .handler = version_handler,
};

static const httpd_uri_t uri_health = {
    .uri = "/health",
    .method = HTTP_GET,
    .handler = health_handler,
};

static const httpd_uri_t uri_ota = {
    .uri = "/ota",
    .method = HTTP_POST,
    .handler = ota_handler,
};

esp_err_t http_ota_server_start(const http_ota_server_config_t *config)
{
    if (s_server != NULL) {
        ESP_LOGW(TAG, "Server already running");
        return ESP_ERR_INVALID_STATE;
    }

    // Apply configuration
    if (config) {
        s_config = *config;
    } else {
        http_ota_server_config_t default_config = HTTP_OTA_SERVER_DEFAULT_CONFIG();
        s_config = default_config;
    }

    // Store version
    if (s_config.firmware_version) {
        strncpy(s_version, s_config.firmware_version, sizeof(s_version) - 1);
        s_version[sizeof(s_version) - 1] = '\0';
    }

    // HTTP server config
    httpd_config_t httpd_config = HTTPD_DEFAULT_CONFIG();
    httpd_config.server_port = s_config.port;
    httpd_config.stack_size = 8192;  // OTA needs more stack
    httpd_config.recv_wait_timeout = 30;  // 30 second timeout for OTA

    ESP_LOGI(TAG, "Starting HTTP OTA server on port %d", s_config.port);

    esp_err_t err = httpd_start(&s_server, &httpd_config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start server: %s", esp_err_to_name(err));
        return err;
    }

    // Register handlers
    httpd_register_uri_handler(s_server, &uri_version);
    httpd_register_uri_handler(s_server, &uri_health);
    httpd_register_uri_handler(s_server, &uri_ota);

    ESP_LOGI(TAG, "HTTP OTA server started");
    ESP_LOGI(TAG, "  GET  /health  - Health check");
    ESP_LOGI(TAG, "  GET  /version - Firmware version");
    ESP_LOGI(TAG, "  POST /ota     - Upload firmware");

    return ESP_OK;
}

esp_err_t http_ota_server_stop(void)
{
    if (s_server == NULL) {
        return ESP_OK;
    }

    esp_err_t err = httpd_stop(s_server);
    s_server = NULL;

    ESP_LOGI(TAG, "HTTP OTA server stopped");
    return err;
}

bool http_ota_server_is_running(void)
{
    return s_server != NULL;
}

const char *http_ota_server_get_version(void)
{
    return s_version;
}
