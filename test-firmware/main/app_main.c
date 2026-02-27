#include "esp_log.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_store.h"
#include "udp_log.h"
#include "wifi_prov.h"
#include "ble_nus.h"
#include "http_server.h"

static const char *TAG = "app_main";

#define FW_VERSION "0.1.0"

static void heartbeat_task(void *arg)
{
    uint32_t tick = 0;
    while (1) {
        ESP_LOGI(TAG, "heartbeat %"PRIu32" | wifi=%d ble=%d",
                 tick++, wifi_prov_is_connected(), ble_nus_is_connected());
        vTaskDelay(pdMS_TO_TICKS(10000));
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "=== Workbench Test Firmware v%s ===", FW_VERSION);

    /* 1. NVS */
    nvs_store_init();

    /* 2. Network stack — must be up before UDP logging */
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    /* 3. UDP debug logging — captures all subsequent logs */
    udp_log_init("192.168.0.87", 5555);

    /* 4. WiFi — STA (stored creds) or AP (captive portal) */
    wifi_prov_init();

    /* 5. In STA mode, wait for WiFi before starting BLE to avoid coexistence
     *    conflicts during association. In AP mode, start BLE immediately. */
    if (!wifi_prov_is_ap_mode()) {
        ESP_LOGI(TAG, "Waiting for WiFi STA connection before starting BLE...");
        for (int i = 0; i < 150 && !wifi_prov_is_connected(); i++)
            vTaskDelay(pdMS_TO_TICKS(100));   /* up to 15s */
    }

    /* 6. BLE — NUS advertisement (no command handler) */
    ble_nus_init();

    /* 7. HTTP server — /status, /ota, /wifi-reset */
    http_server_start();

    /* 8. Heartbeat — periodic log to confirm firmware is alive */
    xTaskCreate(heartbeat_task, "heartbeat", 4096, NULL, 1, NULL);

    ESP_LOGI(TAG, "Init complete, running event-driven");
}
