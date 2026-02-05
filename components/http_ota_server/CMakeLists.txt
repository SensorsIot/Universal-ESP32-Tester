idf_component_register(
    SRCS "http_ota_server.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_http_server app_update esp_ota_ops bootloader_support log nvs_flash
)
