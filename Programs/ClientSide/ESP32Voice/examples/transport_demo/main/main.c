/* Communication-only probe: 10 seconds of silence, then disconnect. No GPIO/audio. */
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "nvs_flash.h"
#include "voice_tcp.h"
#include "sdkconfig.h"
static EventGroupHandle_t state;
static voice_tcp_client_t *client;
#define WIFI_READY BIT0
#define SEND_DONE BIT1
#define RX_DONE BIT2

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) xEventGroupSetBits(state, WIFI_READY);
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) esp_wifi_connect();
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(state, WIFI_READY);
        /* Sender/receiver time out; do not race close or restart an old session. */
        esp_wifi_connect();
    }
}
static void sender(void *unused) {
    static const int16_t silence[512] = {0};
    TickType_t next = xTaskGetTickCount();
    for (int i = 0; i < 313; i++) {
        if (voice_tcp_send_pcm(client, silence, sizeof(silence)) != ESP_OK) break;
        vTaskDelayUntil(&next, pdMS_TO_TICKS(32));
    }
    voice_tcp_request_stop(client);
    xEventGroupSetBits(state, SEND_DONE);
    vTaskDelete(NULL);
}
static void receiver(void *unused) {
    voice_tcp_event_t event;
    while (voice_tcp_receive(client, &event) == ESP_OK) {
        const cJSON *type = cJSON_GetObjectItemCaseSensitive(event.json, "type");
        const cJSON *error = cJSON_GetObjectItemCaseSensitive(event.json, "error");
        bool failed = cJSON_IsString(error);
        if (failed) ESP_LOGE("probe", "Server returned an error");
        /* Only state acknowledgements are expected for silence; do not log content. */
        if (!cJSON_IsString(type) || event.pcm_bytes) failed = true;
        voice_tcp_event_free(&event);
        if (failed) break;
    }
    voice_tcp_request_stop(client);
    xEventGroupSetBits(state, RX_DONE);
    vTaskDelete(NULL);
}
void app_main(void) {
    state = xEventGroupCreate();
    if (!state) return;
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL));
    wifi_config_t station = {0};
    snprintf((char *)station.sta.ssid, sizeof(station.sta.ssid), "%s", CONFIG_VOICE_WIFI_SSID);
    snprintf((char *)station.sta.password, sizeof(station.sta.password), "%s", CONFIG_VOICE_WIFI_PASSWORD);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &station));
    ESP_ERROR_CHECK(esp_wifi_start());
    xEventGroupWaitBits(state, WIFI_READY, pdFALSE, pdTRUE, portMAX_DELAY);
    voice_tcp_config_t config = { .server_ipv4 = CONFIG_VOICE_SERVER_IP, .port = CONFIG_VOICE_SERVER_PORT };
    if (voice_tcp_open(&config, &client) != ESP_OK) { ESP_LOGE("probe", "TCP session open failed"); return; }
    ESP_LOGI("probe", "TCP session opened; testing 10 seconds of silent PCM input");
    if (xTaskCreate(receiver, "voice_rx", 6144, NULL, 5, NULL) != pdPASS) {
        voice_tcp_close(client); return;
    }
    if (xTaskCreate(sender, "voice_tx", 6144, NULL, 5, NULL) != pdPASS) {
        voice_tcp_request_stop(client);
        xEventGroupSetBits(state, SEND_DONE);
    }
    xEventGroupWaitBits(state, SEND_DONE | RX_DONE, pdTRUE, pdTRUE, portMAX_DELAY);
    voice_tcp_close(client);
    client = NULL;
    ESP_LOGI("probe", "Probe ended; no microphone or speaker was used");
}
