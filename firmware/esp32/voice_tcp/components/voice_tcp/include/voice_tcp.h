#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
#include "cJSON.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct voice_tcp_client voice_tcp_client_t;
typedef struct {
    const char *server_ipv4;  /* Wi-Fi must already be connected by your app */
    uint16_t port;           /* default 8766 when zero */
    size_t max_packet_bytes; /* default 1MiB when zero; >=1024, <=1MiB */
    size_t max_audio_bytes;  /* default 512KiB when zero; <=512KiB */
} voice_tcp_config_t;

typedef struct {
    cJSON *json;             /* type, turn_id, state, text, errors, etc. */
    uint8_t *pcm;            /* only audio_chunk/acknowledgement: PCM16LE, 16kHz, mono */
    size_t pcm_bytes;
} voice_tcp_event_t;

/* Blocking connect + session handshake. No Wi-Fi, GPIO or audio-driver setup. */
esp_err_t voice_tcp_open(const voice_tcp_config_t *config, voice_tcp_client_t **out);
/* Call continuously, including silence/during playback. No local VAD. One sender task. */
esp_err_t voice_tcp_send_pcm(voice_tcp_client_t *client, const void *pcm, size_t bytes);
/* One receiver task. Blocking (15s socket timeout); free every successful event. */
esp_err_t voice_tcp_receive(voice_tcp_client_t *client, voice_tcp_event_t *event);
void voice_tcp_event_free(voice_tcp_event_t *event);
/* Notify actual playback; ended only after complete AND all queued audio has played. */
esp_err_t voice_tcp_playback(voice_tcp_client_t *client, const char *turn_id, const char *event);
/* Thread-safe cancellation: shutdown interrupts send/receive, server retires session. */
void voice_tcp_request_stop(voice_tcp_client_t *client);
/* After sender/receiver have exited. Do not call concurrently with other API calls. */
void voice_tcp_close(voice_tcp_client_t *client);

#ifdef __cplusplus
}
#endif
