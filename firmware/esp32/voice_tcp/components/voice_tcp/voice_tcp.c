/* Native TCP voice transport only; hardware and Wi-Fi lifecycle belong to the caller. */
#include "voice_tcp.h"
#include <errno.h>
#include <fcntl.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "lwip/sockets.h"
#include "lwip/inet.h"
#include "mbedtls/base64.h"

#define PACKET_LIMIT (1024 * 1024)
#define AUDIO_LIMIT (512 * 1024)
struct voice_tcp_client {
    int socket;
    SemaphoreHandle_t writer;
    atomic_bool active;
    uint32_t sequence;
    char session[37];
    int generation;
    size_t max_packet, max_audio;
};

static const char *string(const cJSON *json, const char *key) {
    const cJSON *value = cJSON_GetObjectItemCaseSensitive(json, key);
    return cJSON_IsString(value) ? value->valuestring : "";
}
static bool transfer(voice_tcp_client_t *client, void *data, size_t size, bool sending) {
    uint8_t *p = data;
    while (size && atomic_load(&client->active)) {
        int n = sending ? send(client->socket, p, size, 0) : recv(client->socket, p, size, 0);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return false;
        p += n;
        size -= n;
    }
    return size == 0;
}
void voice_tcp_request_stop(voice_tcp_client_t *client) {
    if (client && atomic_exchange(&client->active, false)) shutdown(client->socket, SHUT_RDWR);
}
/* Caller owns writer mutex. Frame header and payload must never interleave. */
static esp_err_t send_locked(voice_tcp_client_t *client, cJSON *json) {
    if (!atomic_load(&client->active)) return ESP_ERR_INVALID_STATE;
    char *text = cJSON_PrintUnformatted(json);
    if (!text) return ESP_ERR_NO_MEM;
    size_t bytes = strlen(text);
    uint32_t header = htonl(bytes);
    bool ok = bytes <= 48000 && transfer(client, &header, 4, true) && transfer(client, text, bytes, true);
    cJSON_free(text);
    if (!ok) voice_tcp_request_stop(client);
    return ok ? ESP_OK : ESP_FAIL;
}
static esp_err_t send_json(voice_tcp_client_t *client, cJSON *json) {
    if (!json) return ESP_ERR_NO_MEM;
    if (xSemaphoreTake(client->writer, pdMS_TO_TICKS(4000)) != pdTRUE) {
        voice_tcp_request_stop(client);
        return ESP_ERR_TIMEOUT;
    }
    esp_err_t result = send_locked(client, json);
    xSemaphoreGive(client->writer);
    return result;
}
static cJSON *read_json(voice_tcp_client_t *client) {
    uint32_t header;
    if (!transfer(client, &header, 4, false)) return NULL;
    size_t bytes = ntohl(header);
    if (!bytes || bytes > client->max_packet) return NULL;
    char *buffer = malloc(bytes + 1);
    if (!buffer) return NULL;
    cJSON *json = NULL;
    if (transfer(client, buffer, bytes, false)) {
        buffer[bytes] = 0;
        json = cJSON_ParseWithLength(buffer, bytes + 1);
    }
    free(buffer);
    if (!cJSON_IsObject(json)) { cJSON_Delete(json); return NULL; }
    return json;
}
/* Nonblocking connect gives a fixed 5s deadline; send/receive have separate timeouts. */
static bool connect_server(int fd, const struct sockaddr_in *address) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) return false;
    int result = connect(fd, (const struct sockaddr *)address, sizeof(*address));
    if (result != 0) {
        if (errno != EINPROGRESS) return false;
        fd_set writable;
        FD_ZERO(&writable);
        FD_SET(fd, &writable);
        struct timeval timeout = { .tv_sec = 5 };
        if (select(fd + 1, NULL, &writable, NULL, &timeout) <= 0) return false;
        int error = 0;
        socklen_t length = sizeof(error);
        if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &length) || error) return false;
    }
    return fcntl(fd, F_SETFL, flags) == 0;
}
esp_err_t voice_tcp_open(const voice_tcp_config_t *config, voice_tcp_client_t **out) {
    if (!out) return ESP_ERR_INVALID_ARG;
    *out = NULL;
    if (!config || !config->server_ipv4 ||
        (config->max_packet_bytes && (config->max_packet_bytes < 1024 || config->max_packet_bytes > PACKET_LIMIT)) ||
        config->max_audio_bytes > AUDIO_LIMIT) return ESP_ERR_INVALID_ARG;
    voice_tcp_client_t *client = calloc(1, sizeof(*client));
    if (!client) return ESP_ERR_NO_MEM;
    client->socket = -1;
    atomic_init(&client->active, false);
    client->max_packet = config->max_packet_bytes ? config->max_packet_bytes : PACKET_LIMIT;
    client->max_audio = config->max_audio_bytes ? config->max_audio_bytes : AUDIO_LIMIT;
    client->writer = xSemaphoreCreateMutex();
    if (!client->writer) { free(client); return ESP_ERR_NO_MEM; }
    struct sockaddr_in address = { .sin_family = AF_INET, .sin_port = htons(config->port ? config->port : 8766) };
    if (inet_pton(AF_INET, config->server_ipv4, &address.sin_addr) != 1) { voice_tcp_close(client); return ESP_ERR_INVALID_ARG; }
    client->socket = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    if (client->socket < 0 || !connect_server(client->socket, &address)) { voice_tcp_close(client); return ESP_FAIL; }
    struct timeval timeout = { .tv_sec = 3 };
    if (setsockopt(client->socket, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout))) { voice_tcp_close(client); return ESP_FAIL; }
    timeout.tv_sec = 15;
    if (setsockopt(client->socket, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout))) { voice_tcp_close(client); return ESP_FAIL; }
    atomic_store(&client->active, true);
    cJSON *start = cJSON_CreateObject();
    if (!start) { voice_tcp_close(client); return ESP_ERR_NO_MEM; }
    bool built = cJSON_AddStringToObject(start, "type", "start") && cJSON_AddNumberToObject(start, "version", 1) &&
                 cJSON_AddStringToObject(start, "audio_profile", "pcm_s16le_16000");
    esp_err_t sent = built ? send_json(client, start) : ESP_ERR_NO_MEM;
    cJSON_Delete(start);
    cJSON *hello = sent == ESP_OK ? read_json(client) : NULL;
    const cJSON *g = cJSON_GetObjectItemCaseSensitive(hello, "generation");
    bool valid = hello && !strcmp(string(hello, "type"), "session") &&
        !strcmp(string(hello, "audio_profile"), "pcm_s16le_16000") && strlen(string(hello, "session_id")) == 36 &&
        cJSON_IsNumber(g) && g->valuedouble >= 0 && g->valuedouble <= INT32_MAX && g->valuedouble == g->valueint;
    if (valid) { strcpy(client->session, string(hello, "session_id")); client->generation = g->valueint; }
    cJSON_Delete(hello);
    if (!valid) { voice_tcp_close(client); return sent != ESP_OK ? sent : ESP_FAIL; }
    *out = client;
    return ESP_OK;
}
esp_err_t voice_tcp_send_pcm(voice_tcp_client_t *client, const void *pcm, size_t bytes) {
    if (!client || !pcm || !bytes || bytes > 32000 || bytes % 2) return ESP_ERR_INVALID_ARG;
    size_t capacity = (bytes + 2) / 3 * 4 + 1, encoded_size = 0;
    unsigned char *encoded = malloc(capacity);
    if (!encoded) return ESP_ERR_NO_MEM;
    if (mbedtls_base64_encode(encoded, capacity, &encoded_size, pcm, bytes)) { free(encoded); return ESP_FAIL; }
    encoded[encoded_size] = 0;
    cJSON *json = cJSON_CreateObject();
    if (!json || !cJSON_AddStringToObject(json, "type", "pcm") ||
        !cJSON_AddStringToObject(json, "audio_base64", (char *)encoded)) {
        cJSON_Delete(json); free(encoded); return ESP_ERR_NO_MEM;
    }
    free(encoded);
    esp_err_t result = ESP_ERR_TIMEOUT;
    if (xSemaphoreTake(client->writer, pdMS_TO_TICKS(4000)) == pdTRUE) {
        if (cJSON_AddNumberToObject(json, "sequence", client->sequence)) {
            result = send_locked(client, json);
            if (result == ESP_OK) client->sequence++;
        } else result = ESP_ERR_NO_MEM;
        xSemaphoreGive(client->writer);
    } else voice_tcp_request_stop(client);
    cJSON_Delete(json);
    return result;
}
esp_err_t voice_tcp_playback(voice_tcp_client_t *client, const char *turn, const char *event) {
    if (!client || !turn || strlen(turn) != 36 || !event ||
        (strcmp(event, "started") && strcmp(event, "ended") && strcmp(event, "failed"))) return ESP_ERR_INVALID_ARG;
    cJSON *json = cJSON_CreateObject();
    if (!json) return ESP_ERR_NO_MEM;
    bool built = cJSON_AddStringToObject(json, "type", "playback") && cJSON_AddStringToObject(json, "turn_id", turn) &&
                 cJSON_AddStringToObject(json, "event", event);
    esp_err_t result = built ? send_json(client, json) : ESP_ERR_NO_MEM;
    cJSON_Delete(json);
    return result;
}
void voice_tcp_event_free(voice_tcp_event_t *event) {
    if (!event) return;
    cJSON_Delete(event->json);
    free(event->pcm);
    memset(event, 0, sizeof(*event));
}
esp_err_t voice_tcp_receive(voice_tcp_client_t *client, voice_tcp_event_t *event) {
    if (!client || !event) return ESP_ERR_INVALID_ARG;
    memset(event, 0, sizeof(*event));
    event->json = read_json(client);
    if (!event->json) goto invalid;
    if (!strcmp(string(event->json, "type"), "error")) return ESP_OK;
    const cJSON *g = cJSON_GetObjectItemCaseSensitive(event->json, "generation");
    if (strcmp(string(event->json, "session_id"), client->session) || !cJSON_IsNumber(g) || g->valuedouble != client->generation) goto invalid;
    const char *type = string(event->json, "type");
    if (strcmp(type, "audio_chunk") && strcmp(type, "acknowledgement")) return ESP_OK;
    const cJSON *rate = cJSON_GetObjectItemCaseSensitive(event->json, "sample_rate");
    const cJSON *channels = cJSON_GetObjectItemCaseSensitive(event->json, "channels");
    if (strcmp(string(event->json, "media_type"), "audio/pcm") || strcmp(string(event->json, "format"), "s16le") ||
        !cJSON_IsNumber(rate) || rate->valuedouble != 16000 || !cJSON_IsNumber(channels) || channels->valuedouble != 1) goto invalid;
    const char *encoded = string(event->json, "audio_base64");
    size_t length = strlen(encoded), size = 0;
    if (!length || length % 4 || length > (client->max_audio + 2) / 3 * 4) goto invalid;
    size_t capacity = length / 4 * 3;
    event->pcm = malloc(capacity);
    if (!event->pcm) goto invalid;
    if (mbedtls_base64_decode(event->pcm, capacity, &size, (const unsigned char *)encoded, length) ||
        !size || size % 2 || size > client->max_audio) goto invalid;
    event->pcm_bytes = size;
    cJSON_DeleteItemFromObjectCaseSensitive(event->json, "audio_base64");
    return ESP_OK;
invalid:
    voice_tcp_event_free(event);
    voice_tcp_request_stop(client);
    return ESP_FAIL;
}
void voice_tcp_close(voice_tcp_client_t *client) {
    if (!client) return;
    voice_tcp_request_stop(client);
    if (client->socket >= 0) close(client->socket);
    if (client->writer) vSemaphoreDelete(client->writer);
    free(client);
}
