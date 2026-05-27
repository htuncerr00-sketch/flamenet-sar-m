/*
 * include/telemetry_task.h — Telemetry task API
 */
#ifndef FW_TELEMETRY_TASK_H
#define FW_TELEMETRY_TASK_H

#ifdef ESP_PLATFORM
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#endif

void telemetry_task(void *pv);

/* Start the telemetry task. Returns pdPASS on ESP, 0 on host. */
#ifdef ESP_PLATFORM
BaseType_t telemetry_task_start(void);
#else
int telemetry_task_start(void);
#endif

#endif
