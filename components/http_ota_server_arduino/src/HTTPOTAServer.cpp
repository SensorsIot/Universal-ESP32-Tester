/**
 * @file HTTPOTAServer.cpp
 * @brief Push-based HTTP OTA Server Implementation (Arduino)
 */

#include "HTTPOTAServer.h"

HTTPOTAServer::HTTPOTAServer(uint16_t port)
    : _server(port)
    , _port(port)
    , _running(false)
    , _onStart(nullptr)
    , _onComplete(nullptr)
    , _onError(nullptr)
    , _onProgress(nullptr)
{
    _version[0] = '\0';
}

void HTTPOTAServer::begin(const char* version) {
    if (_running) {
        return;
    }

    strncpy(_version, version, sizeof(_version) - 1);
    _version[sizeof(_version) - 1] = '\0';

    // Register handlers
    _server.on("/health", HTTP_GET, [this]() { handleHealth(); });
    _server.on("/version", HTTP_GET, [this]() { handleVersion(); });
    _server.on("/ota", HTTP_POST,
        [this]() { /* Response sent in upload handler */ },
        [this]() { handleOTA(); }
    );
    _server.onNotFound([this]() { handleNotFound(); });

    _server.begin();
    _running = true;

    Serial.printf("[OTA] HTTP server started on port %d\n", _port);
    Serial.printf("[OTA] Endpoints:\n");
    Serial.printf("[OTA]   GET  /health  - Health check\n");
    Serial.printf("[OTA]   GET  /version - Firmware version\n");
    Serial.printf("[OTA]   POST /ota     - Upload firmware\n");
}

void HTTPOTAServer::stop() {
    if (!_running) {
        return;
    }

    _server.stop();
    _running = false;
    Serial.println("[OTA] Server stopped");
}

void HTTPOTAServer::handle() {
    if (_running) {
        _server.handleClient();
    }
}

void HTTPOTAServer::handleHealth() {
    _server.send(200, "application/json", "{\"status\":\"ok\"}");
}

void HTTPOTAServer::handleVersion() {
    String json = "{\"version\":\"";
    json += _version;
    json += "\"}";
    _server.send(200, "application/json", json);
}

void HTTPOTAServer::handleOTA() {
    HTTPUpload& upload = _server.upload();

    switch (upload.status) {
        case UPLOAD_FILE_START:
            Serial.printf("[OTA] Receiving firmware: %s\n", upload.filename.c_str());

            // Callback
            if (_onStart) {
                _onStart();
            }

            // Start update
            if (!Update.begin(UPDATE_SIZE_UNKNOWN)) {
                String error = "Update.begin failed: " + String(Update.errorString());
                Serial.printf("[OTA] %s\n", error.c_str());
                if (_onError) {
                    _onError(error.c_str());
                }
            }
            break;

        case UPLOAD_FILE_WRITE:
            if (Update.write(upload.buf, upload.currentSize) != upload.currentSize) {
                String error = "Update.write failed: " + String(Update.errorString());
                Serial.printf("[OTA] %s\n", error.c_str());
                if (_onError) {
                    _onError(error.c_str());
                }
            } else {
                // Progress callback
                if (_onProgress) {
                    _onProgress(upload.totalSize, upload.totalSize + upload.currentSize);
                }
            }
            break;

        case UPLOAD_FILE_END:
            if (Update.end(true)) {
                Serial.printf("[OTA] Update complete: %u bytes\n", upload.totalSize);

                // Send response before reboot
                _server.send(200, "application/json",
                    "{\"status\":\"ok\",\"message\":\"OTA complete, rebooting...\"}");

                // Callback
                if (_onComplete) {
                    _onComplete();
                }

                // Reboot
                Serial.println("[OTA] Rebooting...");
                delay(500);
                ESP.restart();
            } else {
                String error = "Update.end failed: " + String(Update.errorString());
                Serial.printf("[OTA] %s\n", error.c_str());
                _server.send(500, "application/json",
                    "{\"status\":\"error\",\"message\":\"" + error + "\"}");
                if (_onError) {
                    _onError(error.c_str());
                }
            }
            break;

        case UPLOAD_FILE_ABORTED:
            Serial.println("[OTA] Upload aborted");
            Update.abort();
            _server.send(400, "application/json",
                "{\"status\":\"error\",\"message\":\"Upload aborted\"}");
            if (_onError) {
                _onError("Upload aborted");
            }
            break;
    }
}

void HTTPOTAServer::handleNotFound() {
    _server.send(404, "application/json", "{\"error\":\"Not found\"}");
}
