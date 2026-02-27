#pragma once

#include "esp_err.h"
#include <stdbool.h>

#if CONFIG_BT_ENABLED
esp_err_t ble_nus_init(void);
bool      ble_nus_is_connected(void);
#else
static inline esp_err_t ble_nus_init(void) { return ESP_OK; }
static inline bool ble_nus_is_connected(void) { return false; }
#endif
