/*
 * include/health_monitor.h — Runtime stack/heap monitor
 */
#ifndef FW_HEALTH_MONITOR_H
#define FW_HEALTH_MONITOR_H

#ifdef ESP_PLATFORM
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
BaseType_t health_monitor_start(void);
#else
int health_monitor_start(void);
#endif

#endif
