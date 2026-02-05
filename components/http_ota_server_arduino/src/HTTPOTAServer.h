/**
 * @file HTTPOTAServer.h
 * @brief Push-based HTTP OTA Server for ESP32 (Arduino)
 *
 * Simple OTA server that accepts firmware uploads via HTTP POST.
 * Works with any HTTP client - just POST the firmware binary.
 *
 * Usage:
 *   #include <HTTPOTAServer.h>
 *   HTTPOTAServer otaServer;
 *   otaServer.begin("1.0.0");
 *
 * From host:
 *   curl -X POST http://192.168.0.123:8080/ota --data-binary @firmware.bin
 */

#ifndef HTTP_OTA_SERVER_H
#define HTTP_OTA_SERVER_H

#include <Arduino.h>
#include <WebServer.h>
#include <Update.h>

class HTTPOTAServer {
public:
    /**
     * @brief Construct OTA server
     * @param port HTTP server port (default: 8080)
     */
    HTTPOTAServer(uint16_t port = 8080);

    /**
     * @brief Start the OTA server
     * @param version Current firmware version string
     */
    void begin(const char* version = "0.0.0");

    /**
     * @brief Stop the OTA server
     */
    void stop();

    /**
     * @brief Handle incoming requests (call in loop)
     */
    void handle();

    /**
     * @brief Check if server is running
     */
    bool isRunning() const { return _running; }

    /**
     * @brief Get current firmware version
     */
    const char* getVersion() const { return _version; }

    /**
     * @brief Set callback for OTA start
     */
    void onStart(void (*callback)()) { _onStart = callback; }

    /**
     * @brief Set callback for OTA complete (before reboot)
     */
    void onComplete(void (*callback)()) { _onComplete = callback; }

    /**
     * @brief Set callback for OTA error
     */
    void onError(void (*callback)(const char* error)) { _onError = callback; }

    /**
     * @brief Set callback for OTA progress
     * @param callback Function receiving (current, total) bytes
     */
    void onProgress(void (*callback)(size_t current, size_t total)) { _onProgress = callback; }

private:
    WebServer _server;
    uint16_t _port;
    char _version[32];
    bool _running;

    void (*_onStart)();
    void (*_onComplete)();
    void (*_onError)(const char* error);
    void (*_onProgress)(size_t current, size_t total);

    void handleHealth();
    void handleVersion();
    void handleOTA();
    void handleNotFound();
};

#endif // HTTP_OTA_SERVER_H
