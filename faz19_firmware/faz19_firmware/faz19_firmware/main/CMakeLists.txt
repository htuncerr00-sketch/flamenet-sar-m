idf_component_register(
    SRCS
        "app_main.c"
        "telemetry_task.c"
        "telemetry_protocol.c"
        "sensor_pipeline.c"
        "uart_stream.c"
    INCLUDE_DIRS
        "."
        "../include"
    REQUIRES
        driver       # uart driver
        esp_timer
        log
)
