#include "http_server.h"
#include "wifi_prov.h"
#include "ble_nus.h"
#include "ota_update.h"
#include "esp_http_server.h"
#include "esp_ota_ops.h"
#include "esp_log.h"
#include "cJSON.h"

static const char *TAG = "http_srv";

static uint32_t s_boot_count = 0;

void http_server_set_boot_count(uint32_t count)
{
    s_boot_count = count;
}

/* GET /status — JSON with device state */
static esp_err_t status_handler(httpd_req_t *req)
{
    const esp_app_desc_t *app = esp_app_get_description();

    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "project", app->project_name);
    cJSON_AddStringToObject(root, "version", app->version);
    cJSON_AddNumberToObject(root, "boot_count", s_boot_count);
    cJSON_AddBoolToObject(root, "wifi_connected", wifi_prov_is_connected());
    cJSON_AddBoolToObject(root, "ble_connected", ble_nus_is_connected());

    const char *json = cJSON_PrintUnformatted(root);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, json);

    cJSON_free((void *)json);
    cJSON_Delete(root);
    return ESP_OK;
}

/* POST /ota — trigger OTA update */
static esp_err_t ota_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "OTA requested via HTTP");
    esp_err_t err = ota_update_start();

    httpd_resp_set_type(req, "application/json");
    if (err == ESP_OK) {
        httpd_resp_sendstr(req, "{\"status\":\"ok\",\"message\":\"OTA started\"}");
    } else {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Failed to start OTA");
    }
    return ESP_OK;
}

/* POST /wifi-reset — erase credentials and reboot */
static esp_err_t wifi_reset_handler(httpd_req_t *req)
{
    ESP_LOGI(TAG, "WiFi reset requested via HTTP");
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, "{\"status\":\"ok\",\"message\":\"Resetting WiFi...\"}");

    wifi_prov_reset();  /* does not return — reboots */
    return ESP_OK;
}

esp_err_t http_server_start(void)
{
    httpd_handle_t server = NULL;
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 8080;
    config.ctrl_port = 32769;   /* must differ from portal server's default 32768 */

    esp_err_t err = httpd_start(&server, &config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start HTTP server: %s", esp_err_to_name(err));
        return err;
    }

    static const httpd_uri_t status_get = {
        .uri = "/status", .method = HTTP_GET, .handler = status_handler
    };
    static const httpd_uri_t ota_post = {
        .uri = "/ota", .method = HTTP_POST, .handler = ota_handler
    };
    static const httpd_uri_t wifi_reset_post = {
        .uri = "/wifi-reset", .method = HTTP_POST, .handler = wifi_reset_handler
    };

    httpd_register_uri_handler(server, &status_get);
    httpd_register_uri_handler(server, &ota_post);
    httpd_register_uri_handler(server, &wifi_reset_post);

    ESP_LOGI(TAG, "HTTP server started on port 8080 (/status, /ota, /wifi-reset)");
    return ESP_OK;
}
