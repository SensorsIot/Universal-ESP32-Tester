#pragma once

#include "esp_err.h"
#include <stdbool.h>

esp_err_t wifi_prov_init(void);
void      wifi_prov_reset(void);
bool      wifi_prov_is_connected(void);
